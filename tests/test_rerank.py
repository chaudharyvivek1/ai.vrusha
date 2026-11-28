"""The reranker seam: it must change nothing by default, and it must not fail.

Part 14 of `docs/PLAN-V3.md`. The seam exists so the same 40 questions can be run
with and without a second stage on the same day — so the tests that matter are
"identical without one" and "a broken one degrades instead of failing".
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha import rerank
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.harvest.fetch import Document
from vrusha.ingest import ingest_documents
from vrusha.languages import load_registry
from vrusha.rules import load_rules
from vrusha.store.db import Store
from vrusha.store.embed import HashEmbedder
from vrusha.store.query import Hit, retrieve


def _hit(chunk_id: int, score: float = 1.0, text: str = "text") -> Hit:
    return Hit(chunk_id=chunk_id, address=None, text=text, symbol="", kind="prose",
               lang="en", url=f"https://a.test/{chunk_id}", title=f"t{chunk_id}",
               licence="MIT", scores={"primary": score, "fused": score})


class Reverse:
    """A reranker that reverses the order — enough to prove the seam is wired."""

    name = "reverse"

    def available(self) -> bool:
        return True

    def rerank(self, query, hits, k):
        return list(reversed(list(hits)))[:k]


class Exploding:
    name = "exploding"

    def available(self) -> bool:
        return True

    def rerank(self, query, hits, k):
        raise ValueError("the model is having a bad day")


class Inventing:
    """Returns a hit it was never given — the one thing a reranker may not do."""

    name = "inventing"

    def available(self) -> bool:
        return True

    def rerank(self, query, hits, k):
        return [_hit(9999)] + list(hits)[:k]


# ---------------------------------------------------------------------------
# the no-op, and the default
# ---------------------------------------------------------------------------


def test_the_no_op_changes_nothing_and_truncates():
    hits = [_hit(1), _hit(2), _hit(3)]
    out = rerank.NoOpReranker().rerank("q", hits, 2)
    assert [hit.chunk_id for hit in out] == [1, 2]


def test_the_default_backend_is_the_no_op():
    assert rerank.build_reranker({}).name == "none"
    assert rerank.build_reranker({"rerank": {"backend": "none"}}).name == "none"


def test_an_unknown_backend_is_refused_with_the_list():
    with pytest.raises(rerank.RerankError) as error:
        rerank.build_reranker({"rerank": {"backend": "magic"}})
    assert "unknown reranker" in str(error.value)
    assert "none" in str(error.value)


def test_an_unavailable_reranker_falls_back_to_the_no_op(monkeypatch):
    class Missing:
        name = "missing"

        def available(self) -> bool:
            return False

        def rerank(self, query, hits, k):        # pragma: no cover - never called
            raise AssertionError("should not run")

    rerank.register("missing", Missing)
    try:
        assert rerank.build_reranker({"rerank": {"backend": "missing"}}).name == "none"
    finally:
        rerank.RERANKERS.pop("missing", None)


def test_a_registered_backend_is_built_from_config():
    rerank.register("reverse", Reverse)
    try:
        assert rerank.build_reranker({"rerank": {"backend": "reverse"}}).name == "reverse"
    finally:
        rerank.RERANKERS.pop("reverse", None)


# ---------------------------------------------------------------------------
# apply(): reorder, never invent, never fail
# ---------------------------------------------------------------------------


def test_apply_reorders():
    hits = [_hit(1), _hit(2), _hit(3)]
    out = rerank.apply(Reverse(), "q", hits, 3)
    assert [hit.chunk_id for hit in out] == [3, 2, 1]


def test_a_failing_reranker_degrades_and_says_so():
    hits = [_hit(1), _hit(2)]
    notes = []
    out = rerank.apply(Exploding(), "q", hits, 2, notes)
    assert [hit.chunk_id for hit in out] == [1, 2], "the fused order is kept"
    assert notes and "failed" in notes[0]


def test_a_reranker_may_not_invent_a_hit():
    hits = [_hit(1), _hit(2)]
    notes = []
    out = rerank.apply(Inventing(), "q", hits, 3, notes)
    assert 9999 not in [hit.chunk_id for hit in out]
    assert notes and "not given" in notes[0]


def test_no_reranker_is_a_pass_through():
    hits = [_hit(1), _hit(2), _hit(3)]
    assert [hit.chunk_id for hit in rerank.apply(None, "q", hits, 2)] == [1, 2]


def test_apply_on_no_hits_is_empty():
    assert rerank.apply(Reverse(), "q", [], 5) == []


# ---------------------------------------------------------------------------
# the retriever, with and without the seam
# ---------------------------------------------------------------------------


def _store(tmp_path):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    documents = [
        Document(url="https://a.test/ann", title="ann", status=200,
                 text="the vector index uses ivf routing and int8 quantisation. " * 20),
        Document(url="https://a.test/rules", title="rules", status=200,
                 text="a licence gate refuses a page with no licence recorded. " * 20),
        Document(url="https://a.test/model", title="model", status=200,
                 text="the small model trains on a cpu without a graphics card. " * 20),
    ]
    ingest_documents(documents, store, cfg, load_rules(), load_registry(),
                     HashEmbedder(64), licence="MIT", verbose=False)
    return store, cfg


def test_the_no_op_gives_byte_identical_results(tmp_path):
    """The acceptance test: the pipeline is identical with the no-op installed."""
    store, cfg = _store(tmp_path)
    plain = retrieve(store, HashEmbedder(64), "how does the index route", cfg)
    seam = retrieve(store, HashEmbedder(64), "how does the index route", cfg,
                    reranker=rerank.NoOpReranker())
    assert [hit.chunk_id for hit in plain.hits] == [hit.chunk_id for hit in seam.hits]
    assert [round(hit.score, 6) for hit in plain.hits] == \
           [round(hit.score, 6) for hit in seam.hits]
    assert seam.reranked_by == "", "a no-op is not a reranker that ran"
    store.close()


def test_a_real_reranker_actually_reorders_the_results(tmp_path):
    store, cfg = _store(tmp_path)
    plain = retrieve(store, HashEmbedder(64), "how does the index route", cfg,
                     top_k=3)
    reversed_hits = retrieve(store, HashEmbedder(64), "how does the index route",
                             cfg, top_k=3, reranker=Reverse())
    assert [hit.chunk_id for hit in reversed_hits.hits] == \
           list(reversed([hit.chunk_id for hit in plain.hits]))
    assert reversed_hits.reranked_by == "reverse"
    store.close()


def test_a_failing_reranker_does_not_fail_the_query(tmp_path):
    store, cfg = _store(tmp_path)
    result = retrieve(store, HashEmbedder(64), "how does the index route", cfg,
                      reranker=Exploding())
    assert result.hits, "the query still answers"
    assert any("failed" in note for note in result.notes)
    store.close()


def test_the_seam_can_be_turned_off_explicitly(tmp_path):
    store, cfg = _store(tmp_path)
    result = retrieve(store, HashEmbedder(64), "how does the index route", cfg,
                      reranker=Reverse(), rerank_off=True)
    assert result.reranked_by == "", "rerank_off skips even an explicit reranker"
    store.close()


def test_a_configured_reranker_is_picked_up_from_config(tmp_path):
    rerank.register("reverse", Reverse)
    try:
        store, cfg = _store(tmp_path)
        cfg["rerank"] = {"backend": "reverse"}
        result = retrieve(store, HashEmbedder(64), "how does the index route", cfg)
        assert result.reranked_by == "reverse"
        store.close()
    finally:
        rerank.RERANKERS.pop("reverse", None)


# ---------------------------------------------------------------------------
# Part 16: the cross-encoder, tested without the model
# ---------------------------------------------------------------------------


class FakeScorer:
    """Stands in for ONNX: scores by how many query words the passage contains."""

    def __init__(self, available: bool = True):
        self._available = available

    def available(self) -> bool:
        return self._available

    def score(self, query, passages):
        words = set(query.lower().split())
        return [len(words & set((passage or "").lower().split())) / 10.0
                for passage in passages]


def test_the_cross_encoder_reorders_by_the_model_score():
    hits = [_hit(1, text="nothing relevant here"), _hit(2, text="index routing")]
    reranker = rerank.CrossEncoderReranker(alpha=1.0, scorer=FakeScorer())
    out = reranker.rerank("index routing", hits, 2)
    assert [hit.chunk_id for hit in out] == [2, 1]
    assert out[0].scores["cross_encoder"] > out[1].scores["cross_encoder"]


def test_the_cross_encoder_blends_with_the_fused_score():
    """alpha 0 keeps the first stage's order even when the model disagrees."""
    hits = [_hit(1, text="nothing"), _hit(2, text="index routing")]
    reranker = rerank.CrossEncoderReranker(alpha=0.0, scorer=FakeScorer())
    out = reranker.rerank("index routing", hits, 2)
    assert [hit.chunk_id for hit in out] == [1, 2]


def test_a_scorer_that_returns_the_wrong_count_is_refused():
    class Short:
        name = "short"

        def available(self):
            return True

        def score(self, query, passages):
            return [0.5]

    reranker = rerank.CrossEncoderReranker(scorer=Short())
    with pytest.raises(rerank.RerankError) as error:
        reranker.rerank("q", [_hit(1), _hit(2)], 2)
    assert "scores for" in str(error.value)


def test_an_unavailable_cross_encoder_falls_back_to_the_no_op():
    rerank.register("cross-encoder", lambda: rerank.CrossEncoderReranker(
        scorer=FakeScorer(available=False)))
    try:
        assert rerank.build_reranker(
            {"rerank": {"backend": "cross-encoder"}}).name == "none"
    finally:
        rerank.register("cross-encoder", rerank.CrossEncoderReranker)


def test_the_cross_encoder_is_registered():
    assert "cross-encoder" in rerank.RERANKERS
    assert "lexical" in rerank.RERANKERS


def test_the_reranker_sees_a_window_not_every_candidate(tmp_path):
    """A second stage is a re-ranker, not a second search.

    Handing it every candidate made a cross-encoder allocate 755 MB and fail on
    a 2 GB machine -- found by running it.
    """
    seen = {}

    class Spy:
        name = "spy"

        def available(self):
            return True

        def rerank(self, query, hits, k):
            seen["count"] = len(hits)
            return list(hits)[:k]

    store, cfg = _store(tmp_path)
    cfg["rerank"] = {"backend": "spy", "window": 3}
    rerank.register("spy", Spy)
    try:
        retrieve(store, HashEmbedder(64), "how does the index route", cfg)
    finally:
        rerank.RERANKERS.pop("spy", None)
    assert seen["count"] <= 3, f"the reranker saw {seen['count']} candidates"
    store.close()
