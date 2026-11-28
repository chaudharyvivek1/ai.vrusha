"""Delta segments: an append must not rewrite the index.

The claim is about cost, not correctness — so the tests check both: the appended
vectors are findable (correctness), and the base files are not touched (cost).
"""
import os
import sys
import time

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


# ---------------------------------------------------------------------------
# Part 19: upsert and delete
# ---------------------------------------------------------------------------


def _query_for(store, chunk_id):
    vector = np.asarray(store.get_vectors([chunk_id])[1][0], dtype=np.float32)
    return vector / np.linalg.norm(vector)


def test_an_upsert_replaces_the_old_vector(tmp_path):
    """Without a tombstone the base keeps serving the old vector and the chunk
    answers twice — the failure this part exists to prevent."""
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    target = 7
    old_query = _query_for(store, target)

    rng = np.random.default_rng(21)
    fresh = rng.normal(size=DIM).astype(np.float32)
    store.set_vector(target, fresh / np.linalg.norm(fresh))
    store.commit()
    result = delta.upsert(store, index_dir, [target])
    assert result["superseded"] == 1

    # the old direction no longer finds it, the new one does
    old_hits = [c for c, _s in delta.search(index_dir, old_query, 5,
                                            base=built, store=store)]
    new_hits = [c for c, _s in delta.search(index_dir, _query_for(store, target),
                                            5, base=built, store=store)]
    assert target not in old_hits, "the superseded vector still answers"
    assert new_hits[0] == target
    store.close()


def test_a_deleted_chunk_stops_appearing(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    target = 11
    query = _query_for(store, target)
    assert [c for c, _s in delta.search(index_dir, query, 3, base=built,
                                        store=store)][0] == target

    delta.delete(store, index_dir, [target])
    hits = [c for c, _s in delta.search(index_dir, query, 5, base=built,
                                        store=store)]
    assert target not in hits
    store.close()


def test_a_delete_writes_no_vectors(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    result = delta.delete(store, index_dir, [1, 2, 3])
    assert result["rows"] == 0
    assert result["superseded"] == 3
    assert delta.stats(index_dir)["segment_rows"] == 0
    store.close()


def test_a_chunk_can_be_deleted_and_added_again(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    target = 5
    delta.delete(store, index_dir, [target])
    delta.upsert(store, index_dir, [target])
    hits = [c for c, _s in delta.search(index_dir, _query_for(store, target), 3,
                                        base=built, store=store)]
    assert hits[0] == target, "the newest mention wins"
    store.close()


def test_the_newest_segment_wins(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    target = 3
    delta.delete(store, index_dir, [target])
    delta.upsert(store, index_dir, [target])          # newer
    hits = [c for c, _s in delta.search(index_dir, _query_for(store, target), 3,
                                        base=built, store=store)]
    assert target in hits
    store.close()


def test_a_deleted_chunk_is_gone_after_compaction(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    target = 9
    store.deactivate_chunk(target) if hasattr(store, "deactivate_chunk") else None
    store.conn.execute("UPDATE chunks SET active = 0 WHERE chunk_id = ?", (target,))
    store.conn.commit()
    delta.delete(store, index_dir, [target])
    delta.compact(store, index_dir, cfg)

    rebuilt = ann_mod.AnnIndex.load(index_dir)
    assert rebuilt is not None
    hits = [c for c, _s in delta.search(index_dir, _query_for(store, 1), 50,
                                        base=rebuilt, store=store)]
    assert target not in hits, "the base was rebuilt from the store, so the " \
                               "deleted chunk is not in it"
    store.close()


def test_the_merged_search_still_matches_the_store_after_edits(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    rng = np.random.default_rng(31)
    for chunk_id in (2, 4, 6):
        vector = rng.normal(size=DIM).astype(np.float32)
        store.set_vector(chunk_id, vector / np.linalg.norm(vector))
    store.commit()
    delta.upsert(store, index_dir, [2, 4, 6])
    delta.delete(store, index_dir, [8])

    for _ in range(3):
        query = rng.normal(size=DIM).astype(np.float32)
        query /= np.linalg.norm(query)
        hits = {c for c, _s in delta.search(index_dir, query, 5, base=built,
                                            store=store)}
        ids, matrix = store.get_vectors()
        scores = np.asarray(matrix) @ query
        truth = [int(ids[i]) for i in np.argsort(-scores)]
        assert 8 not in hits
        assert hits <= set(truth)
    store.close()


# ---------------------------------------------------------------------------
# Part 20: compaction, safely
# ---------------------------------------------------------------------------


def test_maybe_compact_does_nothing_below_the_threshold(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    delta.append(store, index_dir, _add(store, doc, 5))
    result = delta.maybe_compact(store, index_dir, cfg)
    assert result["compacted"] is False
    assert "threshold" in result["reason"]
    store.close()


def test_safe_compaction_swaps_in_a_new_base(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    delta.append(store, index_dir, _add(store, doc, 20))
    result = delta.safe_compact(store, index_dir, cfg)
    assert result["compacted"] is True
    assert result["segments_removed"] == 1
    assert delta.stats(index_dir)["segments"] == 0
    rebuilt = ann_mod.AnnIndex.load(index_dir)
    assert rebuilt is not None and rebuilt.rows == 120
    store.close()


def test_two_compactions_cannot_race(tmp_path):
    """A lock file, and a second compactor that says so instead of racing."""
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    lock = os.path.join(index_dir, delta.LOCK_FILE)
    with open(lock, "w", encoding="utf-8") as handle:
        handle.write("99999 0")
    result = delta.safe_compact(store, index_dir, cfg)
    assert result["compacted"] is False
    assert "another compaction" in result["reason"]
    os.unlink(lock)
    store.close()


def test_a_stale_lock_is_broken_not_waited_on(tmp_path):
    """A lock nobody releases is an outage, not a safety feature."""
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    delta.append(store, index_dir, _add(store, doc, 10))
    lock = os.path.join(index_dir, delta.LOCK_FILE)
    with open(lock, "w", encoding="utf-8") as handle:
        handle.write("99999 0")
    old = time.time() - 5000
    os.utime(lock, (old, old))
    result = delta.safe_compact(store, index_dir, cfg, stale_lock_s=60)
    assert result["compacted"] is True, "a 5,000-second-old lock was honoured"
    assert not os.path.exists(lock), "the lock was not released"
    store.close()


def test_the_lock_is_released_even_when_building_fails(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)

    class Broken:
        rows = 0

        def save(self, directory):
            raise RuntimeError("disk full")

    original = ann_mod.AnnIndex.build
    ann_mod.AnnIndex.build = classmethod(lambda cls, *a, **k: Broken())
    try:
        with pytest.raises(RuntimeError):
            delta.safe_compact(store, index_dir, cfg)
    finally:
        ann_mod.AnnIndex.build = original
    assert not os.path.exists(os.path.join(index_dir, delta.LOCK_FILE))
    store.close()


def test_a_query_still_answers_after_a_compaction(tmp_path):
    store, cfg, index_dir, built, doc = _setup(tmp_path)
    delta.append(store, index_dir, _add(store, doc, 15))
    delta.safe_compact(store, index_dir, cfg)
    query = np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)
    hits = delta.search(index_dir, query, 3, base=ann_mod.AnnIndex.load(index_dir),
                        store=store)
    assert len(hits) == 3
    store.close()
