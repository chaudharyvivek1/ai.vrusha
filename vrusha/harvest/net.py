"""The network safety layer: where a URL is allowed to take us.

A harvester follows links that strangers wrote. Three things can go wrong that
a hostname check does not catch:

  1. **A redirect to somewhere it should not go.** robots.txt is checked for
     the URL you asked for; if `urllib` follows the redirect itself, the
     destination is never checked. That is how a permitted public page becomes
     a request to `169.254.169.254` (the cloud metadata service) or to
     `127.0.0.1:6379` (whatever is listening on the box).
  2. **A hostname that resolves to a private address.** `localhost`,
     `metadata.google.internal`, or a domain whose A record is `10.0.0.5`.
     Checking the string is not enough; the answer has to be an IP check.
  3. **DNS rebinding.** Resolve-and-check, then connect by name: the second
     resolution can return a different address. The fix is to resolve once,
     validate the address, and connect to *that* address.

So redirects are followed by hand, every hop is re-checked, and the connection
is pinned to the address that was validated. Everything here fails closed and
raises a typed error, never a bare traceback.

Nothing in this module reads a file or touches the store.
"""
from __future__ import annotations

import gzip
import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from functools import partial
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

#: Schemes the harvester will ever request. `file` is here because the offline
#: fixtures are files, and because local harvesting is a first-class path.
ALLOWED_SCHEMES = frozenset({"http", "https", "file"})

#: 3xx codes we follow by hand. 300 and 304 are not redirects in this sense.
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

DEFAULT_MAX_COMPRESSED = 8_000_000
DEFAULT_MAX_DECOMPRESSED = 20_000_000


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

class NetworkError(RuntimeError):
    """Base class: every failure here is typed, so a caller can act on it."""


class UnsafeDestination(NetworkError):
    """A host or address the harvester refuses to connect to."""


class RedirectError(NetworkError):
    """A redirect chain that is too long, looping, or going somewhere refused."""


class ResponseTooLarge(NetworkError):
    """The response exceeded a size limit, compressed or decompressed."""


class DecompressionError(NetworkError):
    """A compressed body that is malformed, truncated or a decompression bomb."""


# ---------------------------------------------------------------------------
# address validation
# ---------------------------------------------------------------------------

def _reject_reason(addr) -> Optional[str]:
    """Why this address is refused, or None if it is acceptable."""
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    checks = [
        (addr.is_loopback, "loopback"),
        # link-local before private: 169.254/16 is both, and "link-local" is the
        # more useful diagnosis -- it is the cloud metadata range.
        (addr.is_link_local, "link-local"),
        (addr.is_private, "private"),
        (addr.is_multicast, "multicast"),
        (addr.is_reserved, "reserved"),
        (addr.is_unspecified, "unspecified"),
    ]
    for flag, name in checks:
        if flag:
            return name
    if not addr.is_global:
        return "not globally routable"
    return None


def is_safe_address(value: str) -> Tuple[bool, str]:
    """Is this IP literal safe to connect to? Returns (safe, reason).

    Handles IPv4, IPv6, IPv4-mapped IPv6, and the alternate IPv4 spellings
    (`2130706433`, `0x7f.1`, `0177.0.0.1`) because `ipaddress` accepts the
    integer form and `getaddrinfo` accepts the rest -- and the check runs on
    the resolved address anyway, so the spelling never matters.
    """
    text = str(value).strip().strip("[]")
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False, "not an IP address"
    reason = _reject_reason(addr)
    return (reason is None), (reason or "public")


def resolve_safe(host: str, port: Optional[int], allow_private: bool = False
                 ) -> Tuple[int, tuple]:
    """Resolve a host and return one validated `(family, sockaddr)` to connect to.

    This is the anti-rebinding step: the address returned here is the address
    the socket connects to, so a second DNS answer cannot redirect the
    connection. Every answer is validated; if any of them is private the host
    is refused outright rather than silently picking a public one, because a
    host that answers with both is a host that is trying something.
    """
    host = (host or "").strip().strip("[]")
    if not host:
        raise UnsafeDestination("empty host")

    # a literal IP needs no resolution, but the same check applies
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        reason = _reject_reason(literal)
        if reason and not allow_private:
            raise UnsafeDestination(f"{host} is a {reason} address")
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        sockaddr = ((str(literal), port or 0, 0, 0) if literal.version == 6
                    else (str(literal), port or 0))
        return family, sockaddr

    try:
        infos = socket.getaddrinfo(host, port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeDestination(f"{host} does not resolve: {exc}") from exc
    if not infos:
        raise UnsafeDestination(f"{host} resolved to no addresses")

    chosen = None
    for family, _socktype, _proto, _canon, sockaddr in infos:
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        reason = _reject_reason(addr)
        if reason and not allow_private:
            raise UnsafeDestination(
                f"{host} resolves to {sockaddr[0]}, a {reason} address")
        if chosen is None:
            chosen = (family, sockaddr)
    if chosen is None:
        raise UnsafeDestination(f"{host} resolved to no usable address")
    return chosen


# ---------------------------------------------------------------------------
# connections pinned to a validated address
# ---------------------------------------------------------------------------

class PinnedHTTPConnection(http.client.HTTPConnection):
    """An HTTPConnection that connects to a validated address, not to a name."""

    def __init__(self, host, port=None, *, resolver=None, **kwargs):
        super().__init__(host, port, **kwargs)
        self._resolver = resolver or (lambda h, p: resolve_safe(h, p))

    def connect(self):
        family, sockaddr = self._resolver(self.host, self.port)
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(sockaddr)
        except OSError:
            sock.close()
            raise
        self.sock = sock


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """The same pinning, with TLS verified against the *hostname*.

    The socket goes to the validated IP; `server_hostname` is the name from the
    URL, so certificate validation still checks the name the certificate was
    issued for. Pinning the address must not weaken TLS.
    """

    def __init__(self, host, port=None, *, resolver=None, context=None, **kwargs):
        super().__init__(host, port, context=context, **kwargs)
        self._resolver = resolver or (lambda h, p: resolve_safe(h, p))

    def connect(self):
        family, sockaddr = self._resolver(self.host, self.port)
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(sockaddr)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


class SafeHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, resolver):
        super().__init__()
        self.resolver = resolver

    def http_open(self, req):
        return self.do_open(partial(PinnedHTTPConnection, resolver=self.resolver), req)


class SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, resolver, context=None):
        super().__init__(context=context)
        self.resolver = resolver

    def https_open(self, req):
        return self.do_open(
            partial(PinnedHTTPSConnection, resolver=self.resolver,
                    context=self._context), req)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow. The caller follows, one checked hop at a time."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener(allow_private: bool = False,
                 context: Optional[ssl.SSLContext] = None) -> urllib.request.OpenerDirector:
    """An opener that pins connections and never follows a redirect by itself."""
    def resolver(host, port):
        return resolve_safe(host, port, allow_private=allow_private)

    context = context or ssl.create_default_context()
    return urllib.request.build_opener(
        SafeHTTPHandler(resolver),
        SafeHTTPSHandler(resolver, context=context),
        NoRedirectHandler(),
    )


# ---------------------------------------------------------------------------
# bounded decompression
# ---------------------------------------------------------------------------

_CHUNK = 64 * 1024


def decompress_capped(raw: bytes, encoding: str,
                      limit: int = DEFAULT_MAX_DECOMPRESSED) -> bytes:
    """Decompress `raw`, refusing to produce more than `limit` bytes.

    The cap is enforced *during* decompression, not after: `zlib` is given a
    `max_length` on every call, so a 1 KB body that expands to 1 GB allocates
    at most `limit` bytes before this raises. Checking the size afterwards
    would mean allocating the bomb first, which is the attack.
    """
    encoding = (encoding or "").strip().lower()
    if encoding in ("", "identity"):
        if len(raw) > limit:
            raise ResponseTooLarge(
                f"body is {len(raw)} bytes, above the {limit}-byte limit")
        return raw

    if encoding in ("gzip", "x-gzip"):
        wbits = 16 + zlib.MAX_WBITS
    elif encoding == "deflate":
        # 'deflate' is zlib-wrapped by the spec and raw-deflate in the wild.
        for candidate in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                return _inflate(raw, candidate, limit)
            except zlib.error:
                continue
        raise DecompressionError("deflate body is neither zlib nor raw-deflate")
    elif encoding in ("br", "zstd"):
        raise DecompressionError(
            f"content-encoding {encoding!r} is not supported; ask for gzip")
    else:
        raise DecompressionError(f"unknown content-encoding {encoding!r}")

    return _inflate(raw, wbits, limit)


def _inflate(raw: bytes, wbits: int, limit: int) -> bytes:
    try:
        engine = zlib.decompressobj(wbits)
    except zlib.error as exc:                     # pragma: no cover - defensive
        raise DecompressionError(f"cannot start decompression: {exc}") from exc

    out = bytearray()
    view = memoryview(raw)
    for start in range(0, len(view), _CHUNK):
        chunk = bytes(view[start:start + _CHUNK])
        while chunk:
            room = limit - len(out) + 1           # +1 so we can detect overflow
            try:
                out += engine.decompress(chunk, room)
            except zlib.error as exc:
                raise DecompressionError(f"malformed compressed body: {exc}") from exc
            if len(out) > limit:
                raise ResponseTooLarge(
                    f"decompressed body exceeds the {limit}-byte limit "
                    f"(refused during decompression, not after)")
            chunk = engine.unconsumed_tail
            if not chunk:
                break
    try:
        out += engine.flush()
    except zlib.error as exc:
        raise DecompressionError(f"truncated compressed body: {exc}") from exc
    if len(out) > limit:
        raise ResponseTooLarge(f"decompressed body exceeds the {limit}-byte limit")
    if not engine.eof:
        raise DecompressionError("compressed body ended before the stream did")
    return bytes(out)


# ---------------------------------------------------------------------------
# the redirect chain
# ---------------------------------------------------------------------------

@dataclass
class Hop:
    url: str
    status: int
    location: str = ""


def same_host(a: str, b: str) -> bool:
    return (urlsplit(a).hostname or "").lower() == (urlsplit(b).hostname or "").lower()


def scheme_of(url: str) -> str:
    return (urlsplit(url).scheme or "").lower()


def check_scheme(url: str) -> str:
    scheme = scheme_of(url)
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeDestination(
            f"scheme {scheme!r} is not allowed (allowed: {sorted(ALLOWED_SCHEMES)})")
    return scheme
