"""sqlite-vec: a third vector engine that agrees with the scan.

The only property worth asserting about a second implementation of the same
search is that it returns the same answers. Everything else — availability,
rebuild, stats — follows from that.
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store.backends import build_backends
from vrusha.store.db import Store

sqlite_vec = pytest.importorskip("sqlite_vec",
                                reason="sqlite-vec is an optional dependency")


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


def _store(tmp_path, chunks=200, dim=32, seed=3):
    store = Store(resolve(_cfg(tmp_path), "db"))
    doc = store.add_document(url="https://a.test/1", title="a", text="body " * 80)
    rng = np.random.default_rng(seed)
    for index in range(chunks):
        chunk = store.add_chunk(doc, f"chunk {index} " + "text " * 10, kind="prose")
        vector = rng.normal(size=dim).astype(np.float32)
        store.set_vector(chunk, vector / np.linalg.norm(vector))
    store.commit()
    return store


def test_it_is_unavailable_until_it_is_built(tmp_path):
    store = _store(tmp_path)
    backend = build_backends(store, _cfg(tmp_path), None)["sqlite-vec"]
    assert backend.available() is False
    assert "no vec0 index" in backend.stats().to_dict()["reason"]
    store.close()


def test_building_reports_what_it_wrote(tmp_path):
    store = _store(tmp_path, chunks=50)
    backend = build_backends(store, _cfg(tmp_path), None)["sqlite-vec"]
    built = backend.build()
    assert built["rows"] == 50 and built["dim"] == 32
    assert os.path.exists(built["path"])
    store.close()


def test_its_answers_match_the_exact_scan(tmp_path):
    """Two engines, one store, the same top three — to six decimal places."""
    store = _store(tmp_path, chunks=300)
    backends = build_backends(store, _cfg(tmp_path), None)
    backends["sqlite-vec"].build()

    rng = np.random.default_rng(11)
    for _ in range(5):
        query = rng.normal(size=32).astype(np.float32)
        query /= np.linalg.norm(query)
        from_vec = backends["sqlite-vec"].search(query, 3)
        from_scan = backends["exact"].search(query, 3)
        assert [chunk for chunk, _score in from_vec] == \
               [chunk for chunk, _score in from_scan]
        for (_a, score_a), (_b, score_b) in zip(from_vec, from_scan):
            assert score_a == pytest.approx(score_b, abs=1e-5)
    store.close()


def test_scores_are_cosine_not_distance(tmp_path):
    """`vec0` returns 1 - cosine; a backend returns a similarity."""
    store = _store(tmp_path, chunks=20)
    backend = build_backends(store, _cfg(tmp_path), None)["sqlite-vec"]
    backend.build()

    chunk_id, vector = store.get_vectors([1])[0][0], store.get_vectors([1])[1][0]
    hits = backend.search(np.asarray(vector, dtype=np.float32), 1)
    assert hits[0][0] == chunk_id
    assert hits[0][1] == pytest.approx(1.0, abs=1e-4), "a vector against itself is 1.0"
    store.close()


def test_rebuilding_replaces_the_file(tmp_path):
    store = _store(tmp_path, chunks=40)
    backend = build_backends(store, _cfg(tmp_path), None)["sqlite-vec"]
    backend.build()
    doc = store.conn.execute("SELECT doc_id FROM documents LIMIT 1").fetchone()[0]
    chunk = store.add_chunk(doc, "one more chunk " * 10, kind="prose")
    vector = np.ones(32, dtype=np.float32)
    store.set_vector(chunk, vector / np.linalg.norm(vector))
    store.commit()

    backend.build()
    assert backend.stats().rows == 41
    store.close()


def test_the_router_offers_it_once_it_exists(tmp_path):
    from vrusha.store.router import Router
    store = _store(tmp_path, chunks=60)
    cfg = _cfg(tmp_path)
    backends = build_backends(store, cfg, None)
    router = Router(store, None, cfg, backends)
    names_before = [entry["name"] for entry in router.report()]
    assert "sqlite-vec" in names_before

    backends["sqlite-vec"].build()
    order, decision = router.plan()
    assert decision.backend in ("native", "sqlite-vec")
    store.close()
