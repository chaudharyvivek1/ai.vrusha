"""Wave 5 regression tests: URL authority, deflate, gzip members, test isolation.

Audit ids: V4-01 (URL parsing), V4-02 (deflate fallback), V4-03 (concatenated
gzip), V4-04 (test isolation). Everything runs against a loopback server
started inside the test; nothing here contacts a public host, and the blocked
destination tests assert that zero connection attempts were made.
"""
import gzip
import http.server
import os
import socket
import sys
import threading
import zlib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS
from vrusha.harvest.fetch import Fetcher, host_of
from vrusha.harvest.net import (
    DecompressionError,
    RedirectError,
    ResponseTooLarge,
    UnsafeDestination,
    canonical_host,
    decompress_capped,
    format_host,
    gunzip_members,
    resolve_safe,
)

PAGE = b"<html><body><p>Real content about retrieval and addresses. " + b"x" * 800 + b"</p></body></html>"
MEMBER_A = b"<html><body>FIRST-MEMBER " + b"a" * 400 + b"</body></html>"
MEMBER_B = b"<html><body>SECOND-MEMBER " + b"b" * 400 + b"</body></html>"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):                                    # noqa: N802
        path = self.path.split("?")[0]
        port = self.server.server_address[1]

        if path == "/robots.txt":
            self._send(200, b"User-agent: *\nAllow: /\n", {"Content-Type": "text/plain"})
        elif path == "/ok":
            self._send(200, PAGE, {"Content-Type": "text/html; charset=utf-8"})
        elif path == "/redir-userinfo":
            self._send(302, b"", {"Location": f"http://user:pass@127.0.0.1:{port}/ok"})
        elif path == "/redir-userinfo-metadata":
            self._send(302, b"", {"Location": "http://user:pass@169.254.169.254/"})
        elif path == "/redir-ipv6-loopback":
            self._send(302, b"", {"Location": f"http://[::1]:{port}/ok"})
        elif path == "/gzip-two":
            self._send(200, gzip.compress(MEMBER_A) + gzip.compress(MEMBER_B),
                       {"Content-Type": "text/html", "Content-Encoding": "gzip"})
        elif path == "/gzip-three":
            self._send(200, gzip.compress(MEMBER_A) + gzip.compress(MEMBER_B)
                       + gzip.compress(MEMBER_A),
                       {"Content-Type": "text/html", "Content-Encoding": "gzip"})
        elif path == "/gzip-bad-second":
            self._send(200, gzip.compress(MEMBER_A) + b"not a gzip member at all",
                       {"Content-Type": "text/html", "Content-Encoding": "gzip"})
        elif path == "/gzip-trailing-nul":
            self._send(200, gzip.compress(MEMBER_A) + b"\x00\x00\x00\x00",
                       {"Content-Type": "text/html", "Content-Encoding": "gzip"})
        elif path == "/deflate-raw":
            self._send(200, zlib.compress(PAGE)[2:-4],
                       {"Content-Type": "text/html; charset=utf-8",
                        "Content-Encoding": "deflate"})
        elif path == "/deflate-zlib":
            self._send(200, zlib.compress(PAGE),
                       {"Content-Type": "text/html; charset=utf-8",
                        "Content-Encoding": "deflate"})
        elif path == "/deflate-corrupt":
            self._send(200, b"\x00\x01\x02 this is not deflate at all",
                       {"Content-Type": "text/html", "Content-Encoding": "deflate"})
        else:
            self._send(404, b"not found")


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def connection_log(monkeypatch):
    """Record every address a socket actually connected to."""
    attempts = []
    real_connect = socket.socket.connect

    def spy(sock, address):
        attempts.append(address[0] if isinstance(address, tuple) else address)
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    return attempts


def cfg():
    c = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    c["http"]["rate_per_host"] = 0
    c["http"]["timeout"] = 5.0
    return c


def loopback_fetcher(**overrides):
    """Only 127.0.0.1 is reachable. Every private range stays blocked."""
    return Fetcher(cfg(), allow_hosts={"127.0.0.1"})


# ---------------------------------------------------------------------------
# V4-01 — URL authority
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("http://example.org/x", "example.org"),
    ("https://example.org:8443/x", "example.org"),
    ("http://EXAMPLE.ORG/x", "example.org"),
    ("http://127.0.0.1:8080/x", "127.0.0.1"),
    ("http://[::1]:8080/x", "::1"),
    ("http://[2001:db8::1]/x", "2001:db8::1"),
    ("https://example.org", "example.org"),
])
def test_v401_canonical_host_parses_every_authority(url, expected):
    assert canonical_host(url) == expected


def test_v401_ipv6_literal_keeps_its_colons():
    """The old regex produced '[' for an IPv6 authority."""
    assert canonical_host("http://[::1]:8080/x") == "::1"
    assert format_host("::1") == "[::1]"
    assert format_host("127.0.0.1") == "127.0.0.1"


def test_v401_credentials_are_refused():
    for url in ("http://user:pass@example.org/x",
                "http://user@example.org/x",
                "https://:pass@example.org/x"):
        with pytest.raises(UnsafeDestination, match="credentials"):
            canonical_host(url)


def test_v401_malformed_authorities_are_refused():
    for url in ("http://", "http:///path", "http://example.org:notaport/x",
                "ftp://example.org/x", ""):
        with pytest.raises(UnsafeDestination):
            canonical_host(url)


def test_v401_idn_is_normalised():
    # the same host written two ways must compare equal
    assert canonical_host("http://münchen.example/x") == canonical_host(
        "http://xn--mnchen-3ya.example/x")


def test_v401_host_of_never_raises():
    assert host_of("http://example.org/x") == "example.org"
    assert host_of("file:///tmp/a.html") == "local"
    assert host_of("http://user:pass@example.org/x") == ""      # refused, not raised
    assert host_of("not a url") == ""


def test_v401_userinfo_redirect_is_refused(server):
    """A redirect carrying credentials must not be followed, even to an
    otherwise permitted host: the credentials are the point of the attack."""
    doc = loopback_fetcher().get(f"{server}/redir-userinfo")
    assert not doc.ok
    assert "credentials" in doc.error


def test_v401_userinfo_redirect_to_metadata_is_refused(server, connection_log):
    doc = loopback_fetcher().get(f"{server}/redir-userinfo-metadata")
    assert not doc.ok
    assert "UnsafeDestination" in doc.error
    assert "169.254.169.254" not in connection_log


def test_v401_redirect_to_ipv6_loopback_is_refused(server, connection_log):
    doc = loopback_fetcher().get(f"{server}/redir-ipv6-loopback")
    assert not doc.ok
    assert "UnsafeDestination" in doc.error
    assert "::1" not in connection_log


def test_v401_allowed_host_compares_the_real_destination(server):
    """The permitted host is the parsed hostname, not the raw netloc."""
    f = loopback_fetcher()
    ok = f.get(f"{server}/ok", allowed_host="127.0.0.1")
    assert ok.ok
    refused = f.get(f"{server}/ok", allowed_host="example.org")
    assert not refused.ok
    assert "RedirectError" in refused.error


def test_v401_robots_uses_the_canonical_host(server, connection_log):
    """robots.txt is fetched from the same host the page request will use."""
    doc = loopback_fetcher().get(f"{server}/ok")
    assert doc.ok
    assert "127.0.0.1" in connection_log


# ---------------------------------------------------------------------------
# V4-02 — deflate
# ---------------------------------------------------------------------------

def test_v402_zlib_wrapped_deflate(server):
    doc = loopback_fetcher().get(f"{server}/deflate-zlib")
    assert doc.ok and "Real content" in doc.text


def test_v402_raw_deflate(server):
    doc = loopback_fetcher().get(f"{server}/deflate-raw")
    assert doc.ok and "Real content" in doc.text


def test_v402_raw_deflate_unit():
    payload = b"raw deflate payload " * 200
    assert decompress_capped(zlib.compress(payload)[2:-4], "deflate", 100_000) == payload
    assert decompress_capped(zlib.compress(payload), "deflate", 100_000) == payload


def test_v402_corrupt_deflate_reports_both_attempts(server):
    doc = loopback_fetcher().get(f"{server}/deflate-corrupt")
    assert not doc.ok
    assert "DecompressionError" in doc.error
    assert "zlib-wrapped" in doc.error and "raw-deflate" in doc.error


def test_v402_size_limit_is_not_mistaken_for_a_format_mismatch():
    """A limit that fires is a limit. It must not be retried as the other
    format and then reported as corrupt data."""
    payload = b"x" * 100_000
    for blob in (zlib.compress(payload), zlib.compress(payload)[2:-4]):
        with pytest.raises(ResponseTooLarge):
            decompress_capped(blob, "deflate", 1_000)


def test_v402_genuine_corruption_is_not_swallowed():
    with pytest.raises(DecompressionError):
        decompress_capped(b"\x00\x01\x02\x03 not deflate", "deflate", 1_000)


# ---------------------------------------------------------------------------
# V4-03 — concatenated gzip members
# ---------------------------------------------------------------------------

def test_v403_two_members_are_both_decompressed(server):
    doc = loopback_fetcher().get(f"{server}/gzip-two")
    assert doc.ok
    assert "FIRST-MEMBER" in doc.text and "SECOND-MEMBER" in doc.text


def test_v403_three_members(server):
    doc = loopback_fetcher().get(f"{server}/gzip-three")
    assert doc.ok
    assert doc.text.count("FIRST-MEMBER") == 2
    assert "SECOND-MEMBER" in doc.text


def test_v403_member_count_is_reported():
    blob = gzip.compress(MEMBER_A) + gzip.compress(MEMBER_B) + gzip.compress(MEMBER_A)
    out, members = gunzip_members(blob, 1_000_000)
    assert members == 3
    assert out == MEMBER_A + MEMBER_B + MEMBER_A


def test_v403_limit_is_cumulative_across_members():
    """Each member fits on its own; together they must not.

    The limit is on the total output, not per member: otherwise a body made of
    many small members is unbounded however small the limit looks.
    """
    blob = gzip.compress(MEMBER_A) + gzip.compress(MEMBER_B)
    budget = 600
    assert len(MEMBER_A) < budget and len(MEMBER_B) < budget
    assert len(MEMBER_A) + len(MEMBER_B) > budget
    with pytest.raises(ResponseTooLarge):
        gunzip_members(blob, budget)


def test_v403_corrupt_second_member_is_rejected(server):
    doc = loopback_fetcher().get(f"{server}/gzip-bad-second")
    assert not doc.ok
    assert "DecompressionError" in doc.error


def test_v403_trailing_nul_padding_is_allowed(server):
    """The gzip spec permits zero padding after the last member."""
    doc = loopback_fetcher().get(f"{server}/gzip-trailing-nul")
    assert doc.ok
    assert "FIRST-MEMBER" in doc.text


def test_v403_invalid_trailing_data_is_rejected():
    blob = gzip.compress(MEMBER_A) + b"garbage that is not a member"
    with pytest.raises(DecompressionError, match="trailing data"):
        gunzip_members(blob, 1_000_000)


def test_v403_single_member_still_works():
    out, members = gunzip_members(gzip.compress(MEMBER_A), 1_000_000)
    assert members == 1 and out == MEMBER_A


def test_v403_empty_body_is_an_error():
    with pytest.raises(DecompressionError):
        gunzip_members(b"", 1_000)


def test_v403_bomb_in_a_second_member_is_capped():
    """A small first member must not buy a free pass for a bomb in the second."""
    bomb = gzip.compress(b"\0" * 20_000_000)
    blob = gzip.compress(MEMBER_A) + bomb
    with pytest.raises(ResponseTooLarge):
        gunzip_members(blob, 1_000_000)


# ---------------------------------------------------------------------------
# V4-04 — test isolation
# ---------------------------------------------------------------------------

def test_v404_the_connection_spy_works(server, connection_log):
    """Control: the spy sees a permitted connection, so a silent zero means
    the request never happened rather than the spy never firing."""
    doc = loopback_fetcher().get(f"{server}/ok")
    assert doc.ok
    assert connection_log, "the spy recorded nothing at all"
    assert all(address == "127.0.0.1" for address in connection_log)


def test_v404_metadata_redirect_makes_no_connection(server, connection_log):
    doc = loopback_fetcher().get(f"{server}/redir-userinfo-metadata")
    assert not doc.ok
    assert "169.254.169.254" not in connection_log
    # the robots fetch and the page fetch both go to loopback, and nowhere else
    assert connection_log, "the spy recorded nothing, so the assertion is vacuous"
    assert set(connection_log) == {"127.0.0.1"}


def test_v404_naming_one_host_does_not_open_the_address_class():
    with pytest.raises(UnsafeDestination):
        resolve_safe("127.0.0.2", 80, allow_hosts={"127.0.0.1"})
    with pytest.raises(UnsafeDestination):
        resolve_safe("169.254.169.254", 80, allow_hosts={"127.0.0.1"})
    with pytest.raises(UnsafeDestination):
        resolve_safe("10.1.2.3", 80, allow_hosts={"127.0.0.1"})


def test_v404_default_fetcher_refuses_loopback_outright():
    strict = Fetcher(cfg())
    with pytest.raises(UnsafeDestination):
        strict._check_destination("http://127.0.0.1:9/", None, first=False)


def test_v404_no_test_uses_the_broad_switch():
    """`allow_private` opens a whole address class; the suite must not use it."""
    import re
    offenders = []
    for name in os.listdir(os.path.join(ROOT, "tests")):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(ROOT, "tests", name), encoding="utf-8").read()
        for match in re.finditer(r"allow_private\s*=\s*True", text):
            line = text[:match.start()].count("\n") + 1
            offenders.append(f"{name}:{line}")
    assert not offenders, f"tests still open every private address: {offenders}"
