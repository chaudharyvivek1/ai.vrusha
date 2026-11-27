"""The router: does it pick the right engine, and does it survive losing one?

No Node and no network here. RagForge is simulated by a stub HTTP server that
speaks its wire format, so the client, the capability gate and the failover are
all tested without a build step. The live two-engine test is
`bench/../live_merge_test.py` in the review bundle.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import ann as ann_mod
from vrusha.store import export as export_mod
from vrusha.store.backends import (Backend, BackendStats, ExactBackend,
                                   NativeBackend, RagForgeBackend,
                                   build_backends)
from vrusha.store.db import Store
from vrusha.store.embed import HashEmbedder
from vrusha.store.router import Decision, Router
from vrusha.store.query import retrieve


# ---------------------------------------------------------------------------
# a store, and stub backends
# ---------------------------------------------------------------------------


def _cfg(root, **backends) -> dict:
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    cfg["ann"] = {**DEFAULTS["ann"], "min_vectors": 0, "target_cell_size": 32}
    cfg["backends"] = {**DEFAULTS["backends"], **backends}
    ensure_dirs(cfg)
    return cfg


def _store_with(root, rows=120, dim=32):
    cfg = _cfg(root)
    store = Store(resolve(cfg, "db"))
    rng = np.random.default_rng(3)
    doc = store.add_document("https://example.test/d", "doc", "corpus")
    points = rng.normal(size=(rows, dim)).astype(np.float32)
    points /= np.linalg.norm(points, axis=1, keepdims=True)
    with store.transaction():
        for index in range(rows):
            chunk_id = store.add_chunk(doc, f"chunk {index}", kind="prose", ordinal=index)
            store.set_vector(chunk_id, points[index])
    store.commit()
    return store, points, cfg


class Stub(Backend):
    """A backend with a chosen name, cost and failure mode."""

    def __init__(self, name, capabilities=frozenset(), delay=0.0, fails=False,
                 available=True, hits=None):
        self.name = name
        self.capabilities = frozenset(capabilities)
        self.delay = delay
        self.fails = fails
        self._available = available
        self.calls = 0
        self.hits = hits if hits is not None else [(1, 0.9), (2, 0.8)]

    def available(self):
        return self._available

    def search(self, vector, k, *, text="", filter=None, nprobe=0):
        self.calls += 1
        if self.fails:
            raise RuntimeError(f"{self.name} is down")
        if self.delay:
            time.sleep(self.delay)
        return list(self.hits)

    def stats(self):
        return BackendStats(self.name, self._available, rows=2, dim=32)


def _router(store, cfg, **stubs) -> Router:
    return Router(store, HashEmbedder(32), cfg, backends=stubs)


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------


def test_a_filter_goes_to_the_only_engine_that_can_filter(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    native, rag = Stub("native", {"vector"}), Stub("ragforge", {"vector", "filter"})
    router = _router(store, cfg, native=native, ragforge=rag, exact=Stub("exact", {"vector", "exact"}))

    order, decision = router.plan(filter={"lang": "en"})
    assert order[0] == "ragforge"
    assert decision.gate == "need"
    assert "filter" in decision.reason


def test_a_filter_with_no_capable_engine_falls_back_and_says_so(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    router = _router(store, cfg, native=Stub("native", {"vector"}),
                     exact=Stub("exact", {"vector", "exact"}))
    order, decision = router.plan(filter={"lang": "en"})
    assert order == ["exact"]
    assert decision.gate == "need"
    assert "filtering after retrieval" in decision.reason


def test_exact_mode_ignores_every_index(tmp_path):
    store, _points, cfg = _store_with(tmp_path, )
    cfg["backends"] = {**cfg["backends"], "mode": "exact"}
    router = _router(store, cfg, native=Stub("native", {"vector"}),
                     exact=Stub("exact", {"vector", "exact"}))
    order, decision = router.plan()
    assert order == ["exact"] and decision.gate == "mode"


def test_the_faster_backend_wins_once_it_has_been_measured(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    router = _router(store, cfg, native=Stub("native", {"vector"}),
                     ragforge=Stub("ragforge", {"vector"}),
                     exact=Stub("exact", {"vector", "exact"}))
    # seed the measurements rather than racing wall-clock sleeps: this is a
    # policy test, and a timing test would be flaky on a shared pod
    router._record("native", 20.0)
    router._record("ragforge", 1.0)

    order, decision = router.plan()
    assert order[0] == "ragforge", decision.reason
    assert decision.gate == "speed"
    assert "faster" in decision.reason


def test_an_unmeasured_backend_gets_one_probe(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    router = _router(store, cfg, native=Stub("native", {"vector"}),
                     ragforge=Stub("ragforge", {"vector"}),
                     exact=Stub("exact", {"vector", "exact"}))
    router._record("native", 1.0)          # only one of them has been timed

    order, decision = router.plan()
    assert order[0] == "ragforge" and decision.gate == "explore"
    assert "never" in decision.reason


def test_a_close_race_does_not_flap(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    router = _router(store, cfg, native=Stub("native", {"vector"}),
                     ragforge=Stub("ragforge", {"vector"}),
                     exact=Stub("exact", {"vector", "exact"}))
    # 5% apart, inside the 20% hysteresis margin: the default should hold
    router._record("native", 10.0)
    router._record("ragforge", 9.5)
    for _ in range(5):
        order, decision = router.plan()
        assert decision.backend == "native", decision.reason


def test_a_clear_margin_does_switch(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    router = _router(store, cfg, native=Stub("native", {"vector"}),
                     ragforge=Stub("ragforge", {"vector"}),
                     exact=Stub("exact", {"vector", "exact"}))
    router._record("native", 10.0)
    router._record("ragforge", 4.0)        # 60% better: outside the margin
    order, decision = router.plan()
    assert decision.backend == "ragforge"


def test_a_failing_backend_is_demoted_and_the_next_one_answers(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    broken = Stub("native", {"vector"}, fails=True)
    healthy = Stub("ragforge", {"vector"})
    router = _router(store, cfg, native=broken, ragforge=healthy,
                     exact=Stub("exact", {"vector", "exact"}))
    vector = np.ones(32, dtype=np.float32) / np.sqrt(32)

    hits, decision, timings = router.search(vector, 5)
    assert hits and decision.gate == "failover"
    assert decision.backend == "ragforge"
    assert router._cooling("native")
    assert broken.calls == 1

    # while cooling, the broken one is not even tried
    router.search(vector, 5)
    assert broken.calls == 1


def test_every_decision_carries_a_reason(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    router = _router(store, cfg, native=Stub("native", {"vector"}),
                     exact=Stub("exact", {"vector", "exact"}))
    order, decision = router.plan()
    assert order and decision.reason and decision.gate
    assert isinstance(decision, Decision)
    assert decision.to_dict()["backend"] == decision.backend


def test_no_backend_at_all_is_an_empty_answer_not_an_error(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    router = _router(store, cfg, native=Stub("native", {"vector"}, available=False),
                     exact=Stub("exact", {"vector", "exact"}, available=False))
    hits, decision, timings = router.search(np.ones(32, dtype=np.float32), 5)
    assert hits == [] and timings == {}
    assert decision.backend == "none"


# ---------------------------------------------------------------------------
# the real backends
# ---------------------------------------------------------------------------


def test_native_and_exact_return_the_same_ids(tmp_path):
    store, points, cfg = _store_with(tmp_path)
    ann_mod.build_for(store, cfg)
    native = NativeBackend(store, cfg)
    exact = ExactBackend(store)
    assert native.available() and exact.available()

    query = points[0]
    assert [c for c, _ in native.search(query, 5)] == \
           [c for c, _ in exact.search(query, 5)]


def test_native_reports_stale_after_a_write(tmp_path):
    store, points, cfg = _store_with(tmp_path)
    ann_mod.build_for(store, cfg)
    doc = store.add_document("https://example.test/new", "new", "new")
    chunk_id = store.add_chunk(doc, "after the build", kind="prose")
    store.set_vector(chunk_id, points[1])
    store.commit()
    stats = NativeBackend(store, cfg).stats()
    assert stats.detail["stale"] is True
    assert not NativeBackend(store, cfg).available()


def test_build_backends_leaves_ragforge_out_until_a_url_is_set(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    assert "ragforge" in build_backends(store, cfg)      # present, to be explainable
    backend = build_backends(store, cfg)["ragforge"]
    assert backend.url == "" or not backend.available()


# ---------------------------------------------------------------------------
# the HTTP client, against a stub that speaks the real wire format
# ---------------------------------------------------------------------------


class _StubServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                        # noqa: N802
        if self.path == "/ready":
            return self._send(200, {"ready": True})
        if self.path == "/stats":
            return self._send(200, {"liveRows": 120, "dim": 32, "segments": 1})
        return self._send(404, {})

    def do_POST(self):                       # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/search":
            assert "vector" in body and body["k"] == 3
            return self._send(200, {"hits": [
                {"id": "11", "score": 0.9, "doc": {}},
                {"id": "7", "score": 0.8, "doc": {}},
            ]})
        if self.path == "/upsert":
            return self._send(200, {"ok": True, "count": len(body.get("docs", []))})
        return self._send(404, {})


@pytest.fixture()
def stub_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubServer)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_ragforge_client_speaks_the_wire_format(stub_server):
    backend = RagForgeBackend(url=stub_server, timeout=3.0)
    assert backend.available()
    hits = backend.search(np.ones(32, dtype=np.float32), 3, text="hello")
    assert hits == [(11, 0.9), (7, 0.8)]
    stats = backend.stats()
    assert stats.rows == 120 and stats.dim == 32


def test_ragforge_client_is_unavailable_when_nothing_listens():
    backend = RagForgeBackend(url="http://127.0.0.1:1", timeout=0.2)
    assert not backend.available()
    assert backend.stats().available is False


# ---------------------------------------------------------------------------
# export, so both engines hold the same rows
# ---------------------------------------------------------------------------


def test_export_jsonl_is_one_row_per_chunk(tmp_path):
    store, _points, cfg = _store_with(tmp_path, rows=40)
    path = str(tmp_path / "corpus.jsonl")
    summary = export_mod.export_to_jsonl(store, path, verbose=False)
    assert summary["chunks"] == 40

    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert len(rows) == 40
    assert set(rows[0]) == {"id", "text", "vector", "meta"}
    assert len(rows[0]["vector"]) == 32
    assert rows[0]["meta"]["url"] == "https://example.test/d"
    assert rows[0]["id"] == "1"


def test_export_batches_keep_the_citable_meta(tmp_path):
    store, _points, cfg = _store_with(tmp_path, rows=25)
    directory = str(tmp_path / "batches")
    manifest = export_mod.export_to_dir(store, directory, size=10, verbose=False)
    assert manifest["chunks"] == 25 and manifest["batches"] == 3
    payload = json.load(open(manifest["files"][0], encoding="utf-8"))
    assert payload["docs"][0]["meta"]["licence"] is None
    assert len(payload["vectors"][0]) == 32


# ---------------------------------------------------------------------------
# retrieve() through the router
# ---------------------------------------------------------------------------


def test_retrieve_records_which_engine_served_it(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    ann_mod.build_for(store, cfg)
    router = Router(store, HashEmbedder(32), cfg)

    result = retrieve(store, HashEmbedder(32), "chunk 3", cfg, router=router)
    assert result.decision is not None
    assert result.decision["backend"] in {"native", "exact", "ragforge"}
    assert any("vector via" in note for note in result.notes)


def test_retrieve_without_a_router_is_unchanged(tmp_path):
    store, _points, cfg = _store_with(tmp_path)
    result = retrieve(store, HashEmbedder(32), "chunk 3", cfg)
    assert result.decision is None
    assert not any("vector via" in note for note in result.notes)


# ---------------------------------------------------------------------------
# the JSON API
# ---------------------------------------------------------------------------


def test_serve_answers_search_and_backends(tmp_path):
    import urllib.request
    from vrusha.serve import serve_in_thread

    store, _points, cfg = _store_with(tmp_path)
    ann_mod.build_for(store, cfg)
    httpd, _thread = serve_in_thread(cfg, HashEmbedder(32), port=0)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"

    try:
        with urllib.request.urlopen(base + "/backends", timeout=5) as response:
            payload = json.loads(response.read())
        names = {item["name"] for item in payload["backends"]}
        assert {"native", "exact", "ragforge"} <= names

        request = urllib.request.Request(
            base + "/search", method="POST",
            data=json.dumps({"text": "chunk 3", "k": 3}).encode(),
            headers={"content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            answer = json.loads(response.read())
        assert answer["backend"] in {"native", "exact"}
        assert answer["reason"]
        assert len(answer["hits"]) <= 3
    finally:
        httpd.shutdown()
        httpd.server_close()
