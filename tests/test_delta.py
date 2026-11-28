"""Delta segments: an append must not rewrite the index.

The claim is about cost, not correctness — so the tests check both: the appended
vectors are findable (correctness), and the base files are not touched (cost).
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import ann as ann_mod
from vrusha.store import delta
from vrusha.store.db import Store

DIM = 32


def _setup(tmp_path, base_rows=100):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    doc = store.add_document(url="https://a.test/1", title="t", text="x " * 60)
    rng = np.random.default_rng(4)
    for index in range(base_rows):
        chunk = store.add_chunk(doc, f"chunk {index} " + "text " * 8, kind="prose")
        vector = rng.normal(size=DIM).astype(np.float32)
        store.set_vector(chunk, vector / np.linalg.norm(vector))
    store.commit()
    index_dir = resolve(cfg, "index")
    os.makedirs(index_dir, exist_ok=True)
    built = ann_mod.AnnIndex.build(store)
    built.save(index_dir)
    return store, cfg, index_dir, built, doc


def _add(store, doc, count, seed=9):
    rng = np.random.default_rng(seed)
    ids = []
    for index in range(count):
        chunk = store.add_chunk(doc, f"new {index} " + "text " * 8, kind="prose")
        vector = rng.normal(size=DIM).astype(np.float32)
        store.set_vector(chunk, vector / np.linalg.norm(vector))
        ids.append(chunk)
    store.commit()
    return ids


def test_an_append_creates_one_segment_and_leaves_the_base_alone(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    before = {name: os.path.getmtime(os.path.join(index_dir, name))
              for name in os.listdir(index_dir)
              if os.path.isfile(os.path.join(index_dir, name))}
    ids = _add(store, doc, 20)
    written = delta.append(store, index_dir, ids)

    assert written["rows"] == 20
    assert written["segments"] == 1
    after = {name: os.path.getmtime(os.path.join(index_dir, name))
             for name in os.listdir(index_dir)
             if os.path.isfile(os.path.join(index_dir, name))}
    assert after == before, "the base files were rewritten by an append"
    store.close()


def test_appended_vectors_are_findable(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    ids = _add(store, doc, 20)
    delta.append(store, index_dir, ids)

    query = np.asarray(store.get_vectors([ids[0]])[1][0], dtype=np.float32)
    hits = delta.search(index_dir, query, 5, base=built, store=store)
    assert ids[0] in [chunk for chunk, _score in hits]
    store.close()


def test_the_merged_search_matches_the_exact_scan(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    ids = _add(store, doc, 30)
    delta.append(store, index_dir, ids)

    rng = np.random.default_rng(11)
    for _ in range(3):
        query = rng.normal(size=DIM).astype(np.float32)
        query /= np.linalg.norm(query)
        hits = delta.search(index_dir, query, 5, base=built, store=store)
        all_ids, matrix = store.get_vectors()
        scores = np.asarray(matrix) @ query
        truth = {int(all_ids[i]) for i in np.argsort(-scores)[:5]}
        assert len({chunk for chunk, _s in hits} & truth) == 5
    store.close()


def test_two_appends_make_two_segments(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    delta.append(store, index_dir, _add(store, doc, 10, seed=1))
    delta.append(store, index_dir, _add(store, doc, 10, seed=2))
    assert delta.stats(index_dir)["segments"] == 2
    assert delta.stats(index_dir)["segment_rows"] == 20
    store.close()


def test_compaction_absorbs_the_segments(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    delta.append(store, index_dir, _add(store, doc, 20))
    assert delta.stats(index_dir)["segments"] == 1

    result = delta.compact(store, index_dir, cfg)
    assert result["segments_removed"] == 1
    assert delta.stats(index_dir)["segments"] == 0

    # and the vectors are still findable, now from the base
    rebuilt = ann_mod.AnnIndex.load(index_dir)
    assert rebuilt is not None and rebuilt.rows == 120
    store.close()


def test_compaction_is_triggered_by_the_segment_cap(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    for _ in range(3):
        delta.append(store, index_dir, _add(store, doc, 5))
    assert delta.should_compact(index_dir, max_segments=3) is True
    assert delta.should_compact(index_dir, max_segments=10) is False
    store.close()


def test_an_empty_append_writes_nothing(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    result = delta.append(store, index_dir, [])
    assert result["rows"] == 0
    assert delta.stats(index_dir)["segments"] == 0
    store.close()


def test_a_search_with_no_segments_still_works(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    query = np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)
    hits = delta.search(index_dir, query, 3, base=built, store=store)
    assert len(hits) == 3
    store.close()


def test_a_corrupt_segment_is_skipped_not_fatal(tmp_path):
    """A half-written file must not take the search down."""
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    delta.append(store, index_dir, _add(store, doc, 10))
    for path in delta.segment_paths(index_dir):
        with open(os.path.join(path, "vectors.npy"), "wb") as handle:
            handle.write(b"not a numpy file")
    query = np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)
    hits = delta.search(index_dir, query, 3, base=built, store=store)
    assert len(hits) == 3, "the base still answers"
    store.close()
