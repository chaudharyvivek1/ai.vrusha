"""Tests for the vector index: does routing find what the full scan finds?

Every test here compares the index against the exact scan, because "it
returned something" is not a property worth asserting. The reference is
`store.get_vectors()` and a float32 dot product — the same code path the
index replaces — so a failure means the index lost a row, quantised it into
oblivion, or answered from a stale snapshot.
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import ann as ann_mod
from vrusha.store.ann import AnnConfig, AnnError, AnnIndex
from vrusha.store.db import Store
from vrusha.store.embed import HashEmbedder
from vrusha.store.query import retrieve


# ---------------------------------------------------------------------------
# a store with a known geometry
# ---------------------------------------------------------------------------


def _cfg(root, **ann) -> dict:
    config = {key: (dict(value) if isinstance(value, dict) else value)
              for key, value in DEFAULTS.items()}
    config["paths"] = dict(DEFAULTS["paths"])
    config["paths"]["root"] = str(root)
    config["ann"] = {**DEFAULTS["ann"], **ann}
    ensure_dirs(config)
    return config


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (matrix / norms).astype(np.float32)


def _clustered(rows: int, dim: int, clusters: int, spread: float = 0.15, seed: int = 7):
    """Points around `clusters` centres — the shape a real corpus has."""
    rng = np.random.default_rng(seed)
    centres = _unit(rng.normal(size=(clusters, dim)))
    labels = rng.integers(0, clusters, size=rows)
    matrix = centres[labels] + rng.normal(scale=spread, size=(rows, dim))
    return _unit(matrix), centres, labels


def _store_with(root, rows: int = 2000, dim: int = 32, clusters: int = 20):
    store = Store(resolve(_cfg(root), "db"))
    doc = store.add_document("https://example.test/doc", "synthetic", "synthetic corpus")
    matrix, centres, labels = _clustered(rows, dim, clusters)
    with store.transaction():
        for index in range(rows):
            chunk_id = store.add_chunk(doc, f"chunk {index} about topic {labels[index]}",
                                       kind="prose", ordinal=index)
            store.set_vector(chunk_id, matrix[index])
    store.commit()
    return store, matrix, centres, labels


def _exact(store, query: np.ndarray, k: int):
    ids, matrix = store.get_vectors()
    scores = _unit(matrix) @ _unit(query.reshape(1, -1))[0]
    order = np.argsort(-scores)[:k]
    return [int(ids[i]) for i in order]


def _recall(found, expected) -> float:
    if not expected:
        return 1.0
    return len(set(found) & set(expected)) / len(expected)


# ---------------------------------------------------------------------------
# the index on its own
# ---------------------------------------------------------------------------


def test_build_then_search_recovers_the_exact_neighbours(tmp_path):
    store, matrix, centres, _labels = _store_with(tmp_path)
    cfg = AnnConfig(target_cell_size=100, iters=10, rescore=128)
    index = AnnIndex.build(store, cfg)

    assert index.rows == matrix.shape[0]
    assert index.dim == matrix.shape[1]
    assert index.cells > 1
    # every row is in exactly one cell, and the CSR covers all of them
    assert int(np.diff(index.ptr).sum()) == index.rows
    assert len(set(index.chunk_ids.tolist())) == index.rows

    rng = np.random.default_rng(99)
    total = 0.0
    for _ in range(20):
        probe = centres[rng.integers(0, centres.shape[0])] + rng.normal(scale=0.05, size=matrix.shape[1])
        found = [chunk_id for chunk_id, _score in index.search(probe, 10, store=store, cfg=cfg)]
        total += _recall(found, _exact(store, probe, 10))
    assert total / 20 >= 0.95, "the index should find almost everything the scan finds"


def test_opening_every_cell_is_identical_to_the_scan(tmp_path):
    store, matrix, centres, _labels = _store_with(tmp_path)
    cfg = AnnConfig(target_cell_size=100)
    index = AnnIndex.build(store, cfg)

    probe = centres[0]
    everything = index.search(probe, 10, store=store, cfg=cfg, nprobe=index.cells)
    assert [cid for cid, _ in everything] == _exact(store, probe, 10)


def test_quantisation_keeps_the_shortlist(tmp_path):
    """int8 picks the candidates; the rescore decides their order.

    What must hold is that the quantised estimate ranks the same rows highly
    and scores them within a rounding error — not that it reproduces the
    exact order, which no 8-bit scalar can promise.
    """
    store, matrix, _centres, _labels = _store_with(tmp_path)
    index = AnnIndex.build(store, AnnConfig(target_cell_size=100))

    query = _unit(matrix[0].reshape(1, -1))[0]
    estimate = index.search(query, 10, store=None)
    exact = index.search(query, 10, store=store)

    assert len(set(cid for cid, _ in estimate) & set(cid for cid, _ in exact)) >= 8
    assert estimate[0][0] == exact[0][0]
    by_id = dict(exact)
    for chunk_id, near in estimate:
        assert abs(near - by_id[chunk_id]) < 0.03


def test_scores_stay_in_cosine_range(tmp_path):
    store, matrix, _centres, _labels = _store_with(tmp_path, rows=300)
    index = AnnIndex.build(store, AnnConfig(target_cell_size=100))
    for chunk_id, score in index.search(matrix[0], 10, store=store):
        assert -1.01 <= score <= 1.01


def test_empty_store_is_refused_with_a_reason(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    store.add_document("https://example.test/empty", "nothing", "no chunks")
    with pytest.raises(AnnError, match="no vectors"):
        AnnIndex.build(store, AnnConfig())


def test_wrong_width_query_is_refused(tmp_path):
    store, matrix, _centres, _labels = _store_with(tmp_path, rows=200)
    index = AnnIndex.build(store, AnnConfig(target_cell_size=100))
    with pytest.raises(AnnError, match="dimensional"):
        index.search(np.zeros(matrix.shape[1] + 3, dtype=np.float32), 5)


# ---------------------------------------------------------------------------
# persistence and freshness
# ---------------------------------------------------------------------------


def test_save_and_load_round_trips(tmp_path):
    store, matrix, centres, _labels = _store_with(tmp_path)
    index = AnnIndex.build(store, AnnConfig(target_cell_size=100))
    directory = str(tmp_path / "index")
    index.save(directory)

    for name in (ann_mod.META, ann_mod.PTR, ann_mod.ROWS,
                 ann_mod.SCALE, ann_mod.CENTROIDS):
        assert os.path.exists(os.path.join(directory, name)), name

    loaded = AnnIndex.load(directory)
    assert loaded is not None
    assert loaded.rows == index.rows and loaded.cells == index.cells
    probe = centres[3]
    assert ([cid for cid, _ in loaded.search(probe, 10, store=store)]
            == [cid for cid, _ in index.search(probe, 10, store=store)])


def test_both_dtypes_round_trip_and_agree(tmp_path):
    """int8 is smaller; float32 needs no conversion. Same answers, both ways."""
    store, matrix, centres, _labels = _store_with(tmp_path, rows=600)

    int8 = AnnIndex.build(store, AnnConfig(target_cell_size=100, dtype="int8"))
    plain = AnnIndex.build(store, AnnConfig(target_cell_size=100, dtype="float32"))
    assert int8.meta["dtype"] == "int8" and plain.meta["dtype"] == "float32"

    probe = centres[2]
    tight = [cid for cid, _ in int8.search(probe, 10, store=store)]
    loose = [cid for cid, _ in plain.search(probe, 10, store=store)]
    assert tight == loose

    for index, expected in ((int8, ann_mod.VEC_I8), (plain, ann_mod.VEC_F32)):
        directory = str(tmp_path / f"idx-{index.meta['dtype']}")
        index.save(directory)
        assert os.path.exists(os.path.join(directory, expected))
        loaded = AnnIndex.load(directory)
        assert loaded is not None and loaded.meta["dtype"] == index.meta["dtype"]
        assert ([cid for cid, _ in loaded.search(probe, 10, store=store)] == loose)


def test_auto_picks_by_the_ram_limit(tmp_path):
    store, matrix, _centres, _labels = _store_with(tmp_path, rows=600)
    wide = AnnIndex.build(store, AnnConfig(target_cell_size=100, ram_limit_mb=512))
    tight = AnnIndex.build(store, AnnConfig(target_cell_size=100, ram_limit_mb=0))
    assert wide.meta["dtype"] == "float32"
    assert tight.meta["dtype"] == "int8"


def test_load_of_a_missing_or_broken_directory_is_none(tmp_path):
    assert AnnIndex.load(str(tmp_path / "nope")) is None
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ann_mod.META).write_text("{not json")
    assert AnnIndex.load(str(broken)) is None


def test_adding_a_vector_makes_the_index_stale(tmp_path):
    store, _matrix, _centres, _labels = _store_with(tmp_path, rows=300)
    cfg = _cfg(tmp_path, target_cell_size=100, min_vectors=0, mode="auto")
    ann_mod.build_for(store, cfg)

    assert ann_mod.load_for(store, cfg) is not None

    doc = store.add_document("https://example.test/new", "new", "new page")
    chunk_id = store.add_chunk(doc, "a chunk added after the build", kind="prose")
    store.set_vector(chunk_id, np.ones(32, dtype=np.float32) / np.sqrt(32))
    store.commit()

    assert ann_mod.load_for(store, cfg) is None, "a stale index must not be used"


def test_stale_index_still_answers_because_the_scan_takes_over(tmp_path):
    store, matrix, _centres, _labels = _store_with(tmp_path, rows=300)
    cfg = _cfg(tmp_path, target_cell_size=100, min_vectors=0, mode="auto")
    ann_mod.build_for(store, cfg)

    doc = store.add_document("https://example.test/new2", "new", "new page")
    chunk_id = store.add_chunk(doc, "retrieval falls back to the exact scan", kind="prose")
    store.set_vector(chunk_id, _unit(matrix[0].reshape(1, -1))[0])
    store.commit()

    result = retrieve(store, HashEmbedder(matrix.shape[1]), "retrieval falls back",
                      cfg, top_k=5)
    assert result.hits, "retrieval must still return hits without a usable index"


# ---------------------------------------------------------------------------
# through the retriever
# ---------------------------------------------------------------------------


def _hits(store, cfg, text="topic", top_k=5):
    ann_mod._CACHE.clear()
    return [hit.chunk_id for hit in
            retrieve(store, HashEmbedder(32), text, cfg, top_k=top_k).hits]


def test_retrieve_with_the_index_agrees_on_the_top_hit(tmp_path):
    """The fused result is what a user sees; the index must not change it.

    A hash embedder is the hard case: its vectors are near-random directions,
    so a query sits far from every centroid and routing has to work by luck.
    That is why this asserts the top hit and not the whole list — and why the
    bench measures the same thing on a realistic geometry.
    """
    store, matrix, _centres, _labels = _store_with(tmp_path)
    cfg = _cfg(tmp_path, target_cell_size=100, min_vectors=0, mode="force")
    ann_mod.build_for(store, cfg)
    off = {**cfg, "ann": {**cfg["ann"], "mode": "off"}}

    same = 0
    for text in ("topic", "chunk 12", "about topic 3", "zzzz"):
        with_index = _hits(store, cfg, text)
        exact = _hits(store, off, text)
        if with_index and exact and with_index[0] == exact[0]:
            same += 1
    assert same >= 3


def test_opening_every_cell_matches_the_exact_path_exactly(tmp_path):
    store, matrix, _centres, _labels = _store_with(tmp_path)
    cfg = _cfg(tmp_path, target_cell_size=100, min_vectors=0, mode="force")
    ann_mod.build_for(store, cfg)
    index = ann_mod.load_for(store, cfg)
    wide = {**cfg, "ann": {**cfg["ann"], "nprobe": index.cells}}
    off = {**cfg, "ann": {**cfg["ann"], "mode": "off"}}
    for text in ("topic", "chunk 12", "about topic 3"):
        assert _hits(store, wide, text, top_k=10) == _hits(store, off, text, top_k=10)


def test_mode_off_never_loads_an_index(tmp_path):
    store, _matrix, _centres, _labels = _store_with(tmp_path, rows=300)
    cfg = _cfg(tmp_path, target_cell_size=100, min_vectors=0, mode="off")
    ann_mod.build_for(store, cfg)
    ann_mod._CACHE.clear()
    assert ann_mod.load_for(store, cfg) is None


def test_mode_auto_ignores_a_small_index(tmp_path):
    store, _matrix, _centres, _labels = _store_with(tmp_path, rows=300)
    cfg = _cfg(tmp_path, target_cell_size=100, min_vectors=1000, mode="auto")
    ann_mod.build_for(store, cfg)
    ann_mod._CACHE.clear()
    assert ann_mod.load_for(store, cfg) is None
    cfg["ann"]["min_vectors"] = 10
    assert ann_mod.load_for(store, cfg) is not None


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_cli_build_stats_drop(tmp_path, capsys):
    store, _matrix, _centres, _labels = _store_with(tmp_path, rows=600)
    store.close()
    root = str(tmp_path)

    assert main(["--root", root, "index", "build"]) == 0
    built = capsys.readouterr().out
    assert "cells" in built and "600" in built

    assert main(["--root", root, "index", "stats"]) == 0
    stats = capsys.readouterr().out
    assert "rows 600" in stats
    assert "stale False" in stats

    assert main(["--root", root, "index", "drop"]) == 0
    assert "removed" in capsys.readouterr().out
    assert ann_mod.AnnIndex.load(str(tmp_path / "index")) is None

    assert main(["--root", root, "index", "stats"]) == 0
    assert "no index" in capsys.readouterr().out
