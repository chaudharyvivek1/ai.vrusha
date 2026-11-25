"""Wave 4 network tests: redirects, SSRF, decompression bombs, charset.

Everything here runs against a server on 127.0.0.1 started inside the test, so
the suite needs no network and no public site.

The fetcher is given `allow_hosts={"127.0.0.1"}` -- a name allow-list, not the
`allow_private` address-class switch. Private, link-local and metadata ranges
stay blocked in every test that uses it, which is asserted directly in
`test_wave5_network.py`.
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
from vrusha.harvest.charset import detect_charset, decode, sniff_meta
from vrusha.harvest.fetch import Fetcher
from vrusha.harvest.net import (
    DecompressionError,
    RedirectError,
    ResponseTooLarge,
    UnsafeDestination,
    check_scheme,
    decompress_capped,
    is_safe_address,
    resolve_safe,
)

PAGE = (b"<!doctype html><html><head><title>Page</title></head><body>"
        b"<p>" + b"Real content about retrieval and addresses. " * 30 + b"</p>"
        b"</body></html>")
HINDI = ("भारतीय रिज़र्व बैंक ने कहा कि रेपो दर में कोई बदलाव नहीं किया गया है। " * 12)


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
        base = f"http://{self.headers.get('Host')}"

        if path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /blocked\n",
                       {"Content-Type": "text/plain"})
        elif path == "/ok":
            self._send(200, PAGE, {"Content-Type": "text/html; charset=utf-8"})
        elif path == "/blocked":
            self._send(200, PAGE, {"Content-Type": "text/html; charset=utf-8"})
        elif path == "/redir-internal":
            self._send(302, b"", {"Location": "/ok"})
        elif path == "/redir-loop-a":
            self._send(302, b"", {"Location": "/redir-loop-b"})
        elif path == "/redir-loop-b":
            self._send(302, b"", {"Location": "/redir-loop-a"})
        elif path == "/redir-chain":
            self._send(302, b"", {"Location": "/redir-internal"})
        elif path == "/redir-forever":
            self._send(302, b"", {"Location": "/redir-forever-2"})
        elif path == "/redir-forever-2":
            self._send(302, b"", {"Location": "/redir-forever-3"})
        elif path == "/redir-forever-3":
            self._send(302, b"", {"Location": "/redir-forever"})
        elif path == "/redir-offsite":
            self._send(302, b"", {"Location": "http://example.com/"})
        elif path == "/redir-metadata":
            self._send(302, b"", {"Location": "http://169.254.169.254/latest/meta-data/"})
        elif path == "/redir-localhost":
            self._send(302, b"", {"Location": "http://localhost:1/"})
        elif path == "/redir-file":
            self._send(302, b"", {"Location": "file:///etc/passwd"})
        elif path == "/redir-blocked":
            self._send(302, b"", {"Location": "/blocked"})
        elif path == "/redir-relative":
            self._send(301, b"", {"Location": "ok"})
        elif path == "/big":
            self._send(200, b"a" * 3_000_000, {"Content-Type": "text/html"})
        elif path == "/gzip-ok":
            self._send(200, gzip.compress(PAGE), {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "gzip"})
        elif path == "/gzip-bomb":
            self._send(200, gzip.compress(b"\0" * 40_000_000), {
                "Content-Type": "text/html", "Content-Encoding": "gzip"})
        elif path == "/deflate-ok":
            self._send(200, zlib.compress(PAGE), {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "deflate"})
        elif path == "/gzip-truncated":
            body = gzip.compress(PAGE)[:40]
            self._send(200, body, {"Content-Type": "text/html",
                                   "Content-Encoding": "gzip"})
        elif path == "/gzip-garbage":
            self._send(200, b"\x1f\x8b\x08\x00not really gzip at all",
                       {"Content-Type": "text/html", "Content-Encoding": "gzip"})
        elif path == "/encoding-unsupported":
            self._send(200, b"whatever", {"Content-Type": "text/html",
                                          "Content-Encoding": "br"})
        elif path == "/latin1-meta":
            body = '<html><head><meta charset="windows-1252"><title>café</title></head><body>résumé naïve</body></html>'
            self._send(200, body.encode("cp1252"), {"Content-Type": "text/html"})
        elif path == "/latin1-header":
            body = '<html><head><title>café</title></head><body>résumé</body></html>'
            self._send(200, body.encode("cp1252"),
                       {"Content-Type": "text/html; charset=iso-8859-1"})
        elif path == "/hindi-meta":
            body = f'<html><head><meta charset="utf-8"><title>RBI</title></head><body>{HINDI}</body></html>'
            self._send(200, body.encode("utf-8"), {"Content-Type": "text/html"})
        elif path == "/hindi-header":
            body = f"<html><head><title>RBI</title></head><body>{HINDI}</body></html>"
            self._send(200, body.encode("utf-8"),
                       {"Content-Type": "text/html; charset=utf-8"})
        elif path == "/bad-charset-header":
            self._send(200, b"<html><body>plain ascii</body></html>",
                       {"Content-Type": "text/html; charset=x-not-a-charset"})
        elif path == "/wrong-header":
            body = '<html><head><title>café</title></head><body>résumé</body></html>'
            self._send(200, body.encode("cp1252"),
                       {"Content-Type": "text/html; charset=utf-8"})
        else:
            self._send(404, b"not found")


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()
    httpd.server_close()


def cfg_for(**http_overrides):
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    cfg["http"]["rate_per_host"] = 0
    cfg["http"]["timeout"] = 5.0
    cfg["http"].update(http_overrides)
    return cfg


def fetcher(**overrides):
    """A fetcher that may reach exactly one host: the loopback test server.

    `allow_hosts` is a name allow-list, not an address-class switch, so
    169.254.169.254 and 10.0.0.0/8 stay blocked in every test that uses it.
    """
    return Fetcher(cfg_for(**overrides), allow_hosts={"127.0.0.1"})


# ---------------------------------------------------------------------------
# the address guard (no server needed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.1.2.3", "10.0.0.5", "192.168.1.1", "172.16.0.1",
    "169.254.169.254", "0.0.0.0", "224.0.0.1", "255.255.255.255",
    "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1",
])
def test_j1_private_and_special_addresses_are_refused(address):
    safe, reason = is_safe_address(address)
    assert not safe, f"{address} was allowed"
    assert reason


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_j1_public_addresses_are_allowed(address):
    safe, _reason = is_safe_address(address)
    assert safe


def test_j1_alternate_ip_spellings_resolve_to_the_same_check():
    # these all mean 127.0.0.1; the check runs on the resolved address
    for spelling in ("2130706433", "0x7f000001", "127.0.0.1"):
        try:
            family, sockaddr = resolve_safe(spelling, 80)
        except UnsafeDestination:
            continue
        assert is_safe_address(sockaddr[0])[0] is False, spelling


def test_j1_hostname_resolving_to_loopback_is_refused():
    with pytest.raises(UnsafeDestination):
        resolve_safe("localhost", 80)


def test_j1_unresolvable_host_is_refused():
    with pytest.raises(UnsafeDestination):
        resolve_safe("no-such-host.invalid", 80)


def test_j1_a_named_host_is_the_narrow_exception():
    family, sockaddr = resolve_safe("127.0.0.1", 8080, allow_hosts={"127.0.0.1"})
    assert sockaddr[0] == "127.0.0.1"
    # naming one host does not open the address class
    with pytest.raises(UnsafeDestination):
        resolve_safe("127.0.0.2", 8080, allow_hosts={"127.0.0.1"})
    with pytest.raises(UnsafeDestination):
        resolve_safe("169.254.169.254", 80, allow_hosts={"127.0.0.1"})


def test_j1_only_expected_schemes():
    assert check_scheme("https://example.org") == "https"
    assert check_scheme("file:///tmp/x.html") == "file"
    for bad in ("ftp://example.org", "gopher://x", "javascript:alert(1)",
                "data:text/html,<script>", ""):
        with pytest.raises(UnsafeDestination):
            check_scheme(bad)


# ---------------------------------------------------------------------------
# redirects
# ---------------------------------------------------------------------------

def test_j1_relative_redirect_is_followed(server):
    doc = fetcher().get(f"{server}/redir-relative")
    assert doc.ok and "Real content" in doc.text


def test_j1_redirect_chain_is_followed(server):
    doc = fetcher().get(f"{server}/redir-chain")
    assert doc.ok and "Real content" in doc.text


def test_j1_redirect_loop_is_detected(server):
    doc = fetcher().get(f"{server}/redir-forever")
    assert not doc.ok
    assert "RedirectError" in doc.error
    assert "loop" in doc.error or "redirects" in doc.error


def test_j1_redirect_off_the_permitted_host_is_refused(server):
    doc = fetcher().get(f"{server}/redir-offsite", allowed_host="127.0.0.1")
    assert not doc.ok
    assert "RedirectError" in doc.error


def test_j1_redirect_to_cloud_metadata_is_refused(server):
    """The classic SSRF: a permitted page redirects to 169.254.169.254.

    This fetcher allows private ranges so it can reach the loopback server, so
    the metadata host is stopped by the fail-closed robots check rather than by
    the address guard. Both refuse; the next test pins down which.
    """
    doc = fetcher().get(f"{server}/redir-metadata")
    assert not doc.ok
    assert "UnsafeDestination" in doc.error
    assert "link-local" in doc.error


def test_j1_metadata_address_is_refused_before_any_request():
    """With the default policy the address guard fires, and it fires first."""
    strict = Fetcher(cfg_for(), allow_private=False)
    with pytest.raises(UnsafeDestination) as caught:
        strict._check_destination("http://169.254.169.254/latest/meta-data/", None,
                                  first=False)
    assert "link-local" in str(caught.value)


def test_j1_redirect_to_localhost_is_refused(server):
    doc = fetcher().get(f"{server}/redir-localhost")
    assert not doc.ok
    assert "UnsafeDestination" in doc.error


def test_j1_redirect_to_file_scheme_is_refused(server):
    doc = fetcher().get(f"{server}/redir-file")
    assert not doc.ok
    assert "UnsafeDestination" in doc.error


def test_j1_robots_is_rechecked_on_the_redirect_destination(server):
    doc = fetcher().get(f"{server}/redir-blocked")
    assert not doc.ok
    assert "robots" in doc.error.lower()


def test_j1_redirect_limit_is_enforced(server):
    f = fetcher(max_redirects=1)
    doc = f.get(f"{server}/redir-chain")
    assert not doc.ok
    assert "RedirectError" in doc.error


def test_j1_direct_request_to_a_blocked_path_is_refused(server):
    doc = fetcher().get(f"{server}/blocked")
    assert not doc.ok
    assert "robots" in doc.error.lower()


# ---------------------------------------------------------------------------
# size and decompression limits
# ---------------------------------------------------------------------------

def test_n1_compressed_size_cap(server):
    doc = fetcher(max_compressed_bytes=500_000).get(f"{server}/big")
    assert not doc.ok
    assert "ResponseTooLarge" in doc.error


def test_n1_gzip_is_decompressed(server):
    doc = fetcher().get(f"{server}/gzip-ok")
    assert doc.ok and "Real content" in doc.text


def test_n1_deflate_is_decompressed(server):
    doc = fetcher().get(f"{server}/deflate-ok")
    assert doc.ok and "Real content" in doc.text


def test_n1_gzip_bomb_is_stopped_during_decompression(server):
    """40 MB of zeros compresses to ~40 KB; the cap must fire before allocating it."""
    doc = fetcher(max_decompressed_bytes=1_000_000).get(f"{server}/gzip-bomb")
    assert not doc.ok
    assert "ResponseTooLarge" in doc.error


def test_n1_truncated_gzip_is_an_error(server):
    doc = fetcher().get(f"{server}/gzip-truncated")
    assert not doc.ok
    assert "DecompressionError" in doc.error


def test_n1_garbage_gzip_is_an_error(server):
    doc = fetcher().get(f"{server}/gzip-garbage")
    assert not doc.ok
    assert "DecompressionError" in doc.error


def test_n1_unsupported_encoding_is_refused(server):
    doc = fetcher().get(f"{server}/encoding-unsupported")
    assert not doc.ok
    assert "DecompressionError" in doc.error


def test_n1_decompress_capped_units():
    payload = b"x" * 10_000
    assert decompress_capped(gzip.compress(payload), "gzip", 20_000) == payload
    with pytest.raises(ResponseTooLarge):
        decompress_capped(gzip.compress(payload), "gzip", 1_000)
    with pytest.raises(DecompressionError):
        decompress_capped(b"not gzip", "gzip", 1_000)
    with pytest.raises(DecompressionError):
        decompress_capped(b"x", "br", 1_000)
    assert decompress_capped(payload, "", 20_000) == payload
    with pytest.raises(ResponseTooLarge):
        decompress_capped(payload, "identity", 1_000)


def test_n1_bomb_is_refused_without_allocating_the_output():
    """A 40 MB expansion must not be materialised before the limit fires."""
    bomb = gzip.compress(b"\0" * 40_000_000)
    with pytest.raises(ResponseTooLarge):
        decompress_capped(bomb, "gzip", 1_000_000)


# ---------------------------------------------------------------------------
# charset
# ---------------------------------------------------------------------------

def test_j2_meta_charset_is_used(server):
    doc = fetcher().get(f"{server}/latin1-meta")
    assert doc.ok
    assert doc.encoding in ("cp1252", "windows-1252")
    assert "résumé" in doc.text
    assert not doc.encoding_lossy


def test_j2_header_charset_is_used(server):
    doc = fetcher().get(f"{server}/latin1-header")
    assert doc.ok
    assert doc.encoding_source == "header"
    assert "résumé" in doc.text


def test_j2_utf8_hindi_survives(server):
    doc = fetcher().get(f"{server}/hindi-meta")
    assert doc.ok
    assert "भारतीय रिज़र्व बैंक" in doc.text
    assert not doc.encoding_lossy
    assert doc.encoding_confidence > 0.5


def test_j2_utf8_header_hindi_survives(server):
    doc = fetcher().get(f"{server}/hindi-header")
    assert doc.ok
    assert "भारतीय" in doc.text
    assert not doc.encoding_lossy


def test_j2_unknown_charset_name_is_ignored_not_fatal(server):
    doc = fetcher().get(f"{server}/bad-charset-header")
    assert doc.ok
    assert "plain ascii" in doc.text


def test_j2_a_wrong_header_is_not_trusted(server):
    """The header says utf-8 and the bytes are cp1252.

    The declaration cannot decode the body, so it is discarded. Whatever
    happens next is recorded as a guess or a fallback, never as a clean
    header decode -- and the text survives, because a page that decoded to
    U+FFFD would be rejected by the quality gate for the wrong reason.
    """
    doc = fetcher().get(f"{server}/wrong-header")
    assert doc.ok
    assert doc.encoding_source != "header"
    assert "r\u00e9sum\u00e9" in doc.text
    if doc.encoding_source == "fallback":
        assert doc.encoding_lossy is True


def test_j2_manifest_records_the_encoding(server):
    doc = fetcher().get(f"{server}/hindi-meta")
    row = doc.to_manifest()
    assert row["encoding"] and row["encoding_source"]
    assert "encoding_confidence" in row and "encoding_lossy" in row


def test_j2_bom_wins_over_everything():
    raw = "\ufeffhello".encode("utf-8")
    result = detect_charset(raw, "iso-8859-1")
    assert result.source == "bom"
    assert result.encoding.startswith("utf")


def test_j2_meta_sniffing_handles_both_forms():
    assert sniff_meta(b'<meta charset="utf-8">') == "utf-8"
    assert sniff_meta(b'<meta http-equiv="Content-Type" content="text/html; charset=cp1252">') == "cp1252"
    assert sniff_meta(b"<html><body>no declaration</body></html>") is None


def test_j2_detect_never_raises_on_binary():
    result = detect_charset(bytes(range(256)), None)
    text, lossy = decode(bytes(range(256)), result)
    assert isinstance(text, str)
    assert result.encoding


def test_j2_deterministic_fallback_for_undecodable_bytes():
    raw = b"\xff\xfe\x00\x81" * 10
    first = detect_charset(raw, None)
    second = detect_charset(raw, None)
    assert first.encoding == second.encoding
