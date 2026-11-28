"""Search to fetch to extract: the loop, without a network.

A stub SearXNG answers the search, a stub web server answers the pages, and the
whole path runs offline: query -> hits -> fetch -> gate -> chunk -> embed ->
store -> tables and JSON-LD. That is the same shape the reference projects
converge on, and it is testable because every hop is a URL we control.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.harvest.fetch import html_to_text
from vrusha.harvest.search import search
from vrusha.store import records
from vrusha.store.db import Store

PAGE = """<html><head><title>Hosting plans</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Starter plan",
 "offers":{"price":"Rs 4,999","priceCurrency":"INR"}}
</script></head><body>
<nav><a href="/">Home</a></nav>
<div class="cookie">We use cookies. Accept?</div>
<article>
<h1>Hosting plans</h1>
<p>""" + ("Our plans renew annually and include support for every customer. " * 12) + """</p>
<table>
  <tr><th>Plan</th><th>Price</th><th>Uptime</th></tr>
  <tr><td>Starter</td><td>Rs 4,999</td><td>99.9%</td></tr>
  <tr><td>Growth</td><td>Rs 5.5 lakh</td><td>99.95%</td></tr>
  <tr><td>Scale</td><td>Rs 2.4 crore</td><td>99.99%</td></tr>
</table>
</article>
<footer>Copyright 2026 Acme Ltd.</footer></body></html>"""


class _Site(BaseHTTPRequestHandler):
    """A web server and a SearXNG on one port: /search returns JSON."""

    def log_message(self, *args):
        pass

    def _send(self, status, body, kind="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(status)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):                                  # noqa: N802
        if self.path.startswith("/search"):
            payload = {"results": [
                {"title": "Hosting plans", "url": self.server.page_url,
                 "content": "plans and prices"},
                {"title": "Broken", "url": self.server.page_url + "/missing",
                 "content": "404"},
            ]}
            return self._send(200, json.dumps(payload), "application/json")
        if self.path.startswith("/missing"):
            return self._send(404, "no")
        return self._send(200, PAGE)


@pytest.fixture()
def site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    port = httpd.server_address[1]
    httpd.page_url = f"http://127.0.0.1:{port}/page"   # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    cfg["http"] = {**DEFAULTS["http"], "allow_private": True, "rate_per_host": 0.0}
    cfg["harvest"] = {**DEFAULTS["harvest"], "extractor": "simple"}
    ensure_dirs(cfg)
    return cfg


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_searxng_backend_parses_the_json_api(site, monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", site)
    hits, notes = search("hosting plans", 5, _cfg("/tmp/x"), backend="searxng")
    assert len(hits) == 2
    assert hits[0].url.endswith("/page")
    assert hits[0].source == "searxng"
    assert hits[0].snippet == "plans and prices"


def test_searxng_without_a_url_falls_through_in_auto(monkeypatch, tmp_path):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    hits, notes = search("anything", 3, _cfg(tmp_path), backend="auto")
    # no key, no url, no network: the ladder ends with nothing, not an exception
    assert isinstance(hits, list)
    assert any("searxng" in note for note in notes)


# ---------------------------------------------------------------------------
# boilerplate
# ---------------------------------------------------------------------------


def test_the_simple_extractor_keeps_a_cookie_banner():
    _title, text, _links = html_to_text(PAGE, extractor="simple")
    assert "cookies" in text


def test_trafilatura_drops_the_banner_when_it_is_installed():
    pytest.importorskip("trafilatura")
    _title, text, _links = html_to_text(PAGE, extractor="auto")
    assert "cookies" not in text
    assert "renew annually" in text


def test_an_unknown_extractor_is_refused():
    with pytest.raises(ValueError, match="unknown extractor"):
        html_to_text(PAGE, extractor="magic")


def test_trafilatura_requested_but_absent_falls_back(tmp_path, monkeypatch):
    """`auto` must never fail because an optional dependency is missing."""
    import builtins
    real_import = builtins.__import__

    def no_trafilatura(name, *args, **kwargs):
        if name == "trafilatura":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_trafilatura)
    _title, text, _links = html_to_text(PAGE, extractor="auto")
    assert "renew annually" in text          # the stripper answered instead


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------


def test_harvest_search_extract_end_to_end(tmp_path, site, monkeypatch, capsys):
    monkeypatch.setenv("SEARXNG_URL", site)
    monkeypatch.setenv("VRUSHA_HTTP_ALLOW_PRIVATE", "true")
    monkeypatch.setenv("VRUSHA_HTTP_RATE_PER_HOST", "0")
    monkeypatch.setenv("VRUSHA_HARVEST_EXTRACTOR", '"simple"')
    root = str(tmp_path)

    code = main(["--root", root, "harvest", "--query", "hosting plans",
                 "--backend", "searxng", "--limit", "1", "--licence", "MIT",
                 "--extract"])
    assert code == 0
    out = capsys.readouterr().out
    assert "stored" in out and "extracted" in out

    store = Store(resolve(_cfg(tmp_path), "db"))
    documents = store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert documents >= 1

    tables = records.list_tables(store)
    kinds = {table["kind"] for table in tables}
    assert "html-table" in kinds            # the <table> on the page
    assert "jsonld" in kinds                # and the Product block beside it

    product = next(t for t in tables if t["kind"] == "jsonld")
    rows = records.table_rows(store, product["table_id"])
    assert rows[0]["offers_price"] == 4999   # "Rs 4,999"


def test_harvest_without_extract_stores_no_tables(tmp_path, site, monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", site)
    monkeypatch.setenv("VRUSHA_HTTP_ALLOW_PRIVATE", "true")
    monkeypatch.setenv("VRUSHA_HTTP_RATE_PER_HOST", "0")
    monkeypatch.setenv("VRUSHA_HARVEST_EXTRACTOR", '"simple"')
    root = str(tmp_path)
    assert main(["--root", root, "harvest", "--query", "hosting plans",
                 "--backend", "searxng", "--limit", "1", "--licence", "MIT"]) == 0

    store = Store(resolve(_cfg(tmp_path), "db"))
    assert records.list_tables(store) == []


def test_extract_document_is_idempotent(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    doc_id = store.add_document(url="https://example.test/p", title="p", text="body")
    first = records.extract_document(store, doc_id, PAGE, "https://example.test/p")
    second = records.extract_document(store, doc_id, PAGE, "https://example.test/p")
    assert first["tables"] == second["tables"]
    assert len(records.list_tables(store)) == second["tables"]
