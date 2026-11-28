"""Anticipation: warm the next query, within a budget that a wrong guess cannot
exceed."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import anticipate


# ---------------------------------------------------------------------------
# the cache is small, bounded and expiring
# ---------------------------------------------------------------------------


def test_a_value_round_trips():
    cache = anticipate.Cache()
    cache.put("q", "result", now=100.0)
    assert cache.get("q", now=101.0) == "result"
    assert cache.hits == 1


def test_an_expired_entry_is_a_miss():
    """A stale prefetch is worse than none: it looks like a warm hit."""
    cache = anticipate.Cache(ttl_s=10)
    cache.put("q", "result", now=100.0)
    assert cache.get("q", now=120.0) is None
    assert cache.misses == 1


def test_the_cache_is_capped():
    cache = anticipate.Cache(limit=2)
    for index in range(5):
        cache.put(f"q{index}", index, now=100.0 + index)
    assert len(cache) == 2
    assert cache.get("q0", now=100.0) is None, "the oldest was not evicted"


def test_a_miss_is_counted():
    cache = anticipate.Cache()
    assert cache.get("nothing", now=1.0) is None
    assert cache.misses == 1


# ---------------------------------------------------------------------------
# prediction
# ---------------------------------------------------------------------------


def _board(tmp_path):
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.db import Store
    store = Store(str(tmp_path / "board.db"))
    return store, Blackboard(store)


def test_open_questions_are_the_first_guess(tmp_path):
    store, board = _board(tmp_path)
    board.append("r", task="t1", kind="question", summary="what is the fee?")
    board.append("r", task="t1", kind="finding", summary="a finding")
    guesses = anticipate.predict(board)
    assert guesses[0] == "what is the fee?"
    store.close()


def test_prediction_is_capped(tmp_path):
    store, board = _board(tmp_path)
    for index in range(10):
        board.append("r", task="t1", kind="question", summary=f"question {index}?")
    assert len(anticipate.predict(board, limit=3)) == 3
    store.close()


def test_no_questions_means_no_guesses(tmp_path):
    store, board = _board(tmp_path)
    board.append("r", task="t1", kind="finding", summary="nothing to ask")
    assert anticipate.predict(board) == []
    store.close()


# ---------------------------------------------------------------------------
# warming, within the budget
# ---------------------------------------------------------------------------


def _store_with_docs(tmp_path):
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.harvest.fetch import Document
    from vrusha.ingest import ingest_documents
    from vrusha.languages import load_registry
    from vrusha.rules import load_rules
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    ingest_documents([Document(url="https://a.test/1", title="ann", status=200,
                               text="the vector index uses ivf routing. " * 30),
                      Document(url="https://a.test/2", title="rules", status=200,
                               text="a licence gate refuses an unlicensed page. " * 30)],
                     store, cfg, load_rules(), load_registry(), HashEmbedder(64),
                     licence="MIT", verbose=False)
    return store, cfg, HashEmbedder(64)


def test_warming_fills_the_cache(tmp_path):
    store, cfg, embedder = _store_with_docs(tmp_path)
    cache = anticipate.Cache()
    report = anticipate.warm(cache, store, embedder, cfg,
                             ["how does the index route?", "what licence?"])
    assert report["warmed"] == 2
    assert len(cache) == 2
    store.close()


def test_a_warm_hit_avoids_retrieval(tmp_path):
    store, cfg, embedder = _store_with_docs(tmp_path)
    cache = anticipate.Cache()
    anticipate.warm(cache, store, embedder, cfg, ["how does the index route?"])
    _result, was_warm = anticipate.lookup(cache, store, embedder, cfg,
                                          "how does the index route?")
    assert was_warm is True
    store.close()


def test_a_cold_lookup_retrieves_and_caches(tmp_path):
    store, cfg, embedder = _store_with_docs(tmp_path)
    cache = anticipate.Cache()
    result, was_warm = anticipate.lookup(cache, store, embedder, cfg,
                                         "how does the index route?")
    assert was_warm is False and result.hits
    assert len(cache) == 1
    store.close()


def test_the_budget_stops_the_work(tmp_path):
    """A wrong guess wastes bounded work, never a run."""
    store, cfg, embedder = _store_with_docs(tmp_path)
    cache = anticipate.Cache()
    report = anticipate.warm(cache, store, embedder, cfg,
                             [f"query {index}" for index in range(20)],
                             budget_ms=0.0)
    assert report["warmed"] == 0
    assert report["skipped"] == 20
    store.close()


def test_a_bad_task_does_not_stop_the_others(tmp_path):
    store, cfg, embedder = _store_with_docs(tmp_path)
    cache = anticipate.Cache()
    report = anticipate.warm(cache, store, embedder, cfg,
                             ["", "how does the index route?"])
    assert report["warmed"] >= 1
    store.close()
