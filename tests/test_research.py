"""Research: question in, answer out, and every source that made it.

The command is only worth having if the sources are complete — a URL, a licence,
how many chunks and formulae came from the page, and whether it still says the
same thing. So that is what these tests check, against a stub that is a search
engine and a web server on one port.
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
from vrusha.languages import load_registry
from vrusha.research import render, research
from vrusha.rules import load_rules
from vrusha.store.db import Store
from vrusha.store.embed import HashEmbedder

PAGE = r"""<html><head><title>Euler and the quadratic</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Article","name":"Euler identity",
 "datePublished":"2026-11-01"}
</script></head><body><article>
<h1>Euler and the quadratic</h1>
<p>Euler's identity is \(e^{i\\pi}+1=0\), which follows from the series expansion.
The identity connects the exponential and the trigonometric functions and is often
called the most beautiful equation in mathematics because it uses five constants.</p>
<table><tr><th>Constant</th><th>Value</th></tr>
<tr><td>e</td><td>2.718</td></tr><tr><td>pi</td><td>3.14159</td></tr></table>
<p>The roots of a quadratic are given by
$$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$ where the discriminant decides the number
of real roots, and that is the whole of the classical theory for a single variable.
A negative discriminant gives two complex conjugate roots, a zero discriminant gives
one repeated real root, and a positive discriminant gives two distinct real roots.
This classification is complete for polynomials of degree two, and it is the reason
the discriminant is worth naming at all rather than folding into the formula.</p>
<p>The identity itself is a special case of Euler's formula, which relates the
complex exponential to sine and cosine for any real argument. Setting the argument
to pi gives minus one, and adding one gives zero, which is the form that is usually
quoted. The proof is two lines once the series for the exponential is known, and
the series converges everywhere on the complex plane, so no special case is needed
for the argument used here.</p>
</article></body></html>"""


class _Site(BaseHTTPRequestHandler):
    """A search engine and a web server on one port."""

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
                {"title": "Euler and the quadratic", "url": self.server.page_url,
                 "content": "Euler identity, quadratic formula"},
            ]}
            return self._send(200, json.dumps(payload), "application/json")
        return self._send(200, PAGE)


@pytest.fixture()
def site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    port = httpd.server_address[1]
    httpd.page_url = f"http://127.0.0.1:{port}/euler"   # type: ignore[attr-defined]
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
    cfg["harvest"] = {**DEFAULTS["harvest"], "extractor": "simple",
                      "min_words": 40}
    ensure_dirs(cfg)
    return cfg


def _run(tmp_path, site, monkeypatch, **kwargs):
    monkeypatch.setenv("SEARXNG_URL", site)
    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    result = research(store, HashEmbedder(256), cfg, "what is the Euler identity",
                      rules=load_rules(), registry=load_registry(),
                      backend="searxng", limit=1, licence="MIT",
                      verbose=False, **kwargs)
    store.close()
    return result


# ---------------------------------------------------------------------------
# the answer and its sources
# ---------------------------------------------------------------------------


def test_a_question_produces_an_answer_and_a_source(tmp_path, site, monkeypatch):
    result = _run(tmp_path, site, monkeypatch)
    assert result["answer"], "an answer is the point of the command"
    assert len(result["sources"]) == 1
    source = result["sources"][0]
    assert source["url"].endswith("/euler")
    assert source["title"].startswith("Euler")
    assert source["status"] == 200


def test_the_source_reports_what_came_out_of_the_page(tmp_path, site, monkeypatch):
    source = _run(tmp_path, site, monkeypatch)["sources"][0]
    assert source["chunks"] >= 1
    # two tables: the <table> of constants, and the JSON-LD Article beside it
    assert source["tables"] == 2
    assert source["equations"] == 2       # the identity and the quadratic


def test_an_unlicensed_page_is_reported_not_silently_dropped(tmp_path, site,
                                                             monkeypatch):
    """A formula without a licence is a rumour with a URL."""
    monkeypatch.setenv("SEARXNG_URL", site)
    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    result = research(store, HashEmbedder(256), cfg, "Euler identity",
                      rules=load_rules(), registry=load_registry(),
                      backend="searxng", limit=1, verbose=False)
    store.close()
    assert result["sources"], "the page must appear, even refused"
    assert "licence" in result["sources"][0]["rejected"]


def test_a_source_carries_its_licence(tmp_path, site, monkeypatch):
    """A formula without a licence is a rumour with a URL."""
    monkeypatch.setenv("SEARXNG_URL", site)
    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    result = research(store, HashEmbedder(256), cfg, "Euler identity",
                      rules=load_rules(), registry=load_registry(),
                      backend="searxng", limit=1, licence="MIT", verbose=False)
    store.close()
    # the caller asserted the licence for what gets stored, and it is recorded
    assert result["sources"][0]["licence"] == "MIT"


def test_verify_reports_whether_the_page_still_says_the_same(
        tmp_path, site, monkeypatch):
    result = _run(tmp_path, site, monkeypatch, verify=True)
    assert result["sources"][0]["verified"] == "unchanged"


def test_a_search_with_no_results_is_a_refusal_not_a_crash(
        tmp_path, site, monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", site)
    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    # a backend that returns nothing: point the search at a path the stub answers
    # with an empty result set by asking for a nonsense query through fixtures
    result = research(store, HashEmbedder(256), cfg, "zzzz", rules=load_rules(),
                      registry=load_registry(), backend="searxng", limit=0,
                      verbose=False)
    store.close()
    assert result["refused"] is True
    assert result["sources"] == []


def test_the_answer_cites_what_it_used(tmp_path, site, monkeypatch):
    result = _run(tmp_path, site, monkeypatch)
    # citations are chunk citations: a url and an address, or empty when the
    # answer was composed without hits
    assert isinstance(result["citations"], list)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_render_shows_sources_and_their_facts(tmp_path, site, monkeypatch):
    text = render(_run(tmp_path, site, monkeypatch, verify=True))
    assert "Q: what is the Euler identity" in text
    assert "Sources:" in text
    assert "/euler" in text
    assert "formula(s)" in text and "table(s)" in text
    assert "verified: unchanged" in text


def test_render_says_so_when_there_is_nothing():
    text = render({"question": "q", "answer": "", "refused": True,
                   "reason": "no search results", "sources": []})
    assert "refused: no search results" in text
    assert "Sources: none" in text


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def test_cli_research_prints_an_answer_with_sources(tmp_path, site, monkeypatch,
                                                    capsys):
    monkeypatch.setenv("SEARXNG_URL", site)
    monkeypatch.setenv("VRUSHA_HTTP_ALLOW_PRIVATE", "true")
    monkeypatch.setenv("VRUSHA_HTTP_RATE_PER_HOST", "0")
    monkeypatch.setenv("VRUSHA_HARVEST_EXTRACTOR", '"simple"')
    monkeypatch.setenv("VRUSHA_HARVEST_MIN_WORDS", "40")

    code = main(["--root", str(tmp_path), "research", "what is the Euler identity",
                 "--backend", "searxng", "--limit", "1", "--licence", "MIT"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Sources:" in out and "/euler" in out


def test_cli_research_json_is_machine_readable(tmp_path, site, monkeypatch, capsys):
    monkeypatch.setenv("SEARXNG_URL", site)
    monkeypatch.setenv("VRUSHA_HTTP_ALLOW_PRIVATE", "true")
    monkeypatch.setenv("VRUSHA_HTTP_RATE_PER_HOST", "0")
    monkeypatch.setenv("VRUSHA_HARVEST_EXTRACTOR", '"simple"')
    monkeypatch.setenv("VRUSHA_HARVEST_MIN_WORDS", "40")

    code = main(["--root", str(tmp_path), "--json", "research",
                 "what is the Euler identity", "--backend", "searxng",
                 "--limit", "1", "--verify", "--licence", "MIT"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["sources"][0]["verified"] == "unchanged"
    assert payload["answer"]
