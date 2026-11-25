"""Regression tests for the Wave 2 and Wave 3 audit findings.

D1 atomic ingestion, F1-F3 checkpoint safety, G1-G2 vector dimensions,
H1-H2 stale rebuilds, B1/B2/K3 distance semantics and signal weighting.
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS
from vrusha.harvest.fetch import Document
from vrusha.ingest import ingest_documents, ingest_paths
from vrusha.languages import load_registry
from vrusha.rules import load_rules
from vrusha.store.address import Address, semantic_similarity
from vrusha.store.db import DimensionError, Store
from vrusha.store.embed import HashEmbedder
from vrusha.store.passes import PassContext, run_passes

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "pages")


def cfg_for(root):
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    cfg["paths"]["root"] = str(root)
    return cfg


class ExplodingEmbedder:
    name, dim = "exploding", 256

    def encode(self, texts):
        raise RuntimeError("embedding backend died mid-batch")


class ShortEmbedder:
    """Returns fewer vectors than asked for: a backend bug worth catching."""
    name, dim = "short", 256

    def encode(self, texts):
        return np.zeros((max(0, len(texts) - 1), 256), dtype=np.float32)


@pytest.fixture
def env(tmp_path):
    rules, registry = load_rules(), load_registry()
    cfg = cfg_for(tmp_path)
    store = Store(str(tmp_path / "v.db"))
    yield store, cfg, rules, registry
    store.close()


# ---------------------------------------------------------------------------
# D1: ingestion is atomic
# ---------------------------------------------------------------------------

def test_d1_failed_embedding_leaves_nothing(env):
    store, cfg, rules, registry = env
    body = "Retrieval augmented generation answers from retrieved context. " * 40
    doc = Document(url="https://example.org/x", title="X", text=body,
                   raw_chars=len(body), licence="MIT", status=200)
    report = ingest_documents([doc], store, cfg, rules, registry, ExplodingEmbedder())
    assert report.stored == 0 and report.rejected == 1
    assert store.document_count() == 0
    assert store.chunk_count(active_only=False) == 0
    assert store.conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0


def test_d1_short_embedding_response_is_a_failure(env):
    store, cfg, rules, registry = env
    body = "Retrieval augmented generation answers from retrieved context. " * 40
    doc = Document(url="https://example.org/y", title="Y", text=body,
                   raw_chars=len(body), licence="MIT", status=200)
    report = ingest_documents([doc], store, cfg, rules, registry, ShortEmbedder())
    assert report.stored == 0
    assert store.chunk_count(active_only=False) == 0


def test_d1_one_bad_document_does_not_roll_back_the_good_one(env):
    store, cfg, rules, registry = env

    class Flaky:
        name, dim = "flaky", 256
        calls = 0

        def encode(self, texts):
            Flaky.calls += 1
            if Flaky.calls == 2:
                raise RuntimeError("second document fails")
            return np.ones((len(texts), 256), dtype=np.float32) / 16.0

    good_a = Document(url="https://example.org/a", title="A",
                      text="Alpha document about retrieval and addresses. " * 30,
                      raw_chars=2000, licence="MIT", status=200)
    bad = Document(url="https://example.org/b", title="B",
                   text="Beta document about tokenizers and vocabulary. " * 30,
                   raw_chars=2000, licence="MIT", status=200)
    good_c = Document(url="https://example.org/c", title="C",
                      text="Gamma document about rules and precedence levels. " * 30,
                      raw_chars=2000, licence="MIT", status=200)
    report = ingest_documents([good_a, bad, good_c], store, cfg, rules, registry, Flaky())
    assert report.stored == 2 and report.rejected == 1
    urls = {r[0] for r in store.conn.execute("SELECT url FROM documents").fetchall()}
    assert urls == {"https://example.org/a", "https://example.org/c"}


def test_d1_reingesting_the_same_document_is_skipped(env):
    store, cfg, rules, registry = env
    body = "Retrieval augmented generation answers from retrieved context. " * 40
    doc = Document(url="https://example.org/dup", title="D", text=body,
                   raw_chars=len(body), licence="MIT", status=200)
    first = ingest_documents([doc], store, cfg, rules, registry, HashEmbedder(256))
    chunks_after_first = store.chunk_count(active_only=False)
    second = ingest_documents([doc], store, cfg, rules, registry, HashEmbedder(256))
    assert first.stored == 1
    assert second.stored == 0
    assert second.rejected == 1
    # either the duplicate gate or the already-stored guard may catch it; what
    # matters is that no second copy of the chunks was written
    assert store.chunk_count(active_only=False) == chunks_after_first
    assert store.document_count() == 1


# ---------------------------------------------------------------------------
# G1-G2: vector dimensions
# ---------------------------------------------------------------------------

def test_g1_wrong_dimension_is_refused(env):
    store, cfg, rules, registry = env
    ingest_paths([FIXTURES], store, cfg, rules, registry, HashEmbedder(256), licence="MIT")
    chunk_id = store.all_chunks()[0]["chunk_id"]
    with pytest.raises(DimensionError):
        store.set_vector(chunk_id, np.ones(128, dtype=np.float32))
    assert store.vector_dim() == 256


def test_g1_non_finite_vector_is_refused(env):
    store, cfg, rules, registry = env
    ingest_paths([FIXTURES], store, cfg, rules, registry, HashEmbedder(256), licence="MIT")
    chunk_id = store.all_chunks()[0]["chunk_id"]
    bad = np.ones(256, dtype=np.float32)
    bad[3] = np.nan
    with pytest.raises(DimensionError):
        store.set_vector(chunk_id, bad)


def test_g1_clear_vectors_is_the_reembed_path(env):
    store, cfg, rules, registry = env
    ingest_paths([FIXTURES], store, cfg, rules, registry, HashEmbedder(256), licence="MIT")
    removed = store.clear_vectors()
    assert removed > 0
    assert store.embed_dim() is None
    # a different width is now legal again
    chunk_id = store.all_chunks()[0]["chunk_id"]
    store.set_vector(chunk_id, np.ones(64, dtype=np.float32))
    assert store.vector_dim() == 64


def test_g2_width_mismatch_is_reported_not_silent(env):
    from vrusha.store.query import retrieve
    store, cfg, rules, registry = env
    ingest_paths([FIXTURES], store, cfg, rules, registry, HashEmbedder(256), licence="MIT")
    result = retrieve(store, HashEmbedder(128), "numeric address tree", cfg)
    assert any("width mismatch" in note for note in result.notes)


# ---------------------------------------------------------------------------
# F1-F3: checkpoint safety
# ---------------------------------------------------------------------------

torch = pytest.importorskip("torch")

from vrusha.generate.model import (  # noqa: E402
    CheckpointError,
    ModelConfig,
    TinyLM,
    config_from_payload,
    load_payload,
)


def test_f1_a_pickled_payload_is_refused_without_executing(tmp_path):
    marker = tmp_path / "executed.txt"

    class Benign:
        def __reduce__(self):
            return (open, (str(marker), "w"))

    path = tmp_path / "untrusted.pt"
    torch.save({"model": Benign(), "config": {}}, path)
    with pytest.raises(CheckpointError):
        load_payload(str(path))
    assert not marker.exists(), "the payload executed during a safe load"


def test_f1_a_clean_checkpoint_needs_no_opt_in(tmp_path):
    cfg = ModelConfig.preset("tiny", 96)
    model = TinyLM(cfg)
    path = str(tmp_path / "clean.pt")
    model.save_checkpoint(path, step=3)
    payload = load_payload(path)          # weights_only=True, no opt-in
    assert payload["step"] == 3
    assert set(payload) >= {"model", "config"}


def test_f2_a_real_checkpoint_round_trips(tmp_path):
    cfg = ModelConfig.preset("tiny", 96)
    model = TinyLM(cfg)
    path = str(tmp_path / "good.pt")
    model.save_checkpoint(path, step=7)
    loaded, payload = TinyLM.from_checkpoint(path)
    assert payload["step"] == 7
    assert loaded.params() == model.params()
    for a, b in zip(model.state_dict().values(), loaded.state_dict().values()):
        assert torch.equal(a, b)


def test_f3_bad_config_is_rejected():
    payload = {"model": {}, "config": {"vocab_size": 96, "d_model": 65, "n_layer": 1,
                                       "n_head": 2, "n_ctx": 64}}
    with pytest.raises(CheckpointError):
        config_from_payload(payload)


def test_f3_unknown_config_key_is_rejected():
    payload = {"model": {}, "config": {"vocab_size": 96, "d_model": 64, "n_layer": 1,
                                       "n_head": 2, "n_ctx": 64, "sneaky": 1}}
    with pytest.raises(CheckpointError):
        config_from_payload(payload)


def test_f3_wrong_shapes_are_rejected(tmp_path):
    cfg = ModelConfig.preset("tiny", 96)
    model = TinyLM(cfg)
    payload = {"model": dict(model.state_dict()), "config": payload_config(cfg), "step": 1}
    payload["model"]["embed.weight"] = torch.zeros(96, 8)   # wrong width
    path = str(tmp_path / "bad.pt")
    torch.save(payload, path)
    with pytest.raises(CheckpointError):
        TinyLM.from_checkpoint(path)


def test_f3_missing_tensor_is_rejected(tmp_path):
    cfg = ModelConfig.preset("tiny", 96)
    model = TinyLM(cfg)
    state = dict(model.state_dict())
    state.pop("norm.weight")
    path = str(tmp_path / "missing.pt")
    torch.save({"model": state, "config": payload_config(cfg), "step": 1}, path)
    with pytest.raises(CheckpointError):
        TinyLM.from_checkpoint(path)


def test_f3_a_non_checkpoint_file_is_rejected(tmp_path):
    path = str(tmp_path / "junk.pt")
    torch.save({"hello": "world"}, path)
    with pytest.raises(CheckpointError):
        TinyLM.from_checkpoint(path)


def payload_config(cfg):
    from dataclasses import asdict
    return asdict(cfg)


# ---------------------------------------------------------------------------
# H1-H2: rebuilds replace, they do not accumulate
# ---------------------------------------------------------------------------

def _synthetic_store(tmp_path, n=24, seed=3):
    store = Store(str(tmp_path / "s.db"))
    rng = np.random.default_rng(seed)
    doc = store.add_document("https://e.org/s", "s", "x", licence="MIT")
    for i in range(n):
        chunk_id = store.add_chunk(doc, f"chunk {i} " + f"token{i} " * 20,
                                   kind="paragraph", lang="en")
        vector = rng.normal(size=64).astype(np.float32)
        store.set_vector(chunk_id, vector / np.linalg.norm(vector))
    store.commit()
    return store


def test_h1_graph_rebuild_removes_stale_edges(tmp_path):
    store = _synthetic_store(tmp_path)
    cfg = cfg_for(tmp_path)
    rules, registry = load_rules(), load_registry()
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry),
               only=["analyse.graph"])
    first = store.conn.execute("SELECT COUNT(*) FROM edges WHERE kind='near'").fetchone()[0]
    cfg["passes"]["graph_neighbours"] = 1
    result = run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry),
                        only=["analyse.graph"])
    worst = store.conn.execute(
        "SELECT MAX(c) FROM (SELECT COUNT(*) c FROM edges WHERE kind='near' GROUP BY src)"
    ).fetchone()[0]
    assert worst == 1, "a rebuild with k=1 must leave exactly one neighbour per chunk"
    assert result[0]["replaced"] == first
    store.close()


def test_h2_centroids_are_pruned_to_the_live_clustering(tmp_path):
    store = _synthetic_store(tmp_path, n=40)
    cfg = cfg_for(tmp_path)
    rules, registry = load_rules(), load_registry()
    cfg["passes"]["cluster_max"] = 24
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry),
               only=["analyse.cluster", "analyse.centroid"])
    cfg["passes"]["cluster_max"] = 2
    result = run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry),
                        only=["analyse.cluster", "analyse.centroid"])
    rows = store.conn.execute("SELECT COUNT(*) FROM centroids").fetchone()[0]
    live = store.conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT addr_domain, addr_family, addr_kind, "
        "addr_cluster FROM chunks WHERE active=1 AND addr_cluster IS NOT NULL)").fetchone()[0]
    assert rows == live
    assert result[-1]["pruned"] >= 0
    store.close()


# ---------------------------------------------------------------------------
# B1/B2/K3: distance is semantic, the address signal is a tiebreaker
# ---------------------------------------------------------------------------

def test_b1_family_ids_are_categorical_not_arithmetic():
    base = Address.of(2, 2, 3, 1, 1)
    near = Address.of(2, 3, 3, 1, 1)
    far = Address.of(2, 20, 3, 1, 1)
    assert semantic_similarity(base, near) == semantic_similarity(base, far) == 0.25


def test_b1_domain_mismatch_is_zero():
    assert semantic_similarity(Address.of(1, 1, 3, 1, 1), Address.of(2, 1, 3, 1, 1)) == 0.0


def test_b1_cluster_distance_comes_from_centroids():
    a, b = Address.of(1, 1, 3, 1, 1), Address.of(1, 1, 3, 2, 1)
    same = {(1, 1, 3, 1): (1.0, 0.0), (1, 1, 3, 2): (1.0, 0.0)}
    opposite = {(1, 1, 3, 1): (1.0, 0.0), (1, 1, 3, 2): (0.0, 1.0)}
    assert semantic_similarity(a, b, same) == pytest.approx(1.0)
    assert semantic_similarity(a, b, opposite) == pytest.approx(0.0)
    # no centroids: neutral, never a number invented from the ids
    assert semantic_similarity(a, b) == pytest.approx(0.6)


def test_b1_ordinal_only_matters_inside_a_cluster():
    a = Address.of(1, 1, 3, 1, 1)
    b = Address.of(1, 1, 3, 1, 5)
    c = Address.of(1, 1, 3, 2, 5)
    assert semantic_similarity(a, b) < 1.0
    assert semantic_similarity(a, b) > semantic_similarity(a, c)


def test_b2_fusion_weight_is_a_tiebreaker():
    weights = DEFAULTS["query"]["fusion"]
    assert weights["address"] < min(weights["bm25"], weights["vector"])


def test_k3_an_address_hint_cannot_displace_a_supported_hit(env):
    from vrusha.store.query import retrieve
    store, cfg, rules, registry = env
    ingest_paths([FIXTURES], store, cfg, rules, registry, HashEmbedder(256), licence="MIT")
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry))
    plain = retrieve(store, HashEmbedder(256),
                     "why fuse keyword and vector search by rank", cfg, top_k=3)
    hinted = retrieve(store, HashEmbedder(256),
                      "why fuse keyword and vector search by rank", cfg, top_k=3,
                      address_hint=Address.parse("2"))
    assert plain.hits and hinted.hits
    assert hinted.hits[0].chunk_id == plain.hits[0].chunk_id
    top = hinted.hits[0]
    assert top.scores.get("bm25", 0) > 0 or top.scores.get("vector", 0) > 0
