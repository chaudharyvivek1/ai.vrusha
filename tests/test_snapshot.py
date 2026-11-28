"""Snapshots: a pinned generation is never deleted.

Part 21 of `docs/PLAN-V3.md`. The property that matters is one sentence — a
compaction must not pull the files out from under a reader — and every test here
is a way that could fail.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import snapshot


def test_a_fresh_directory_has_generation_zero(tmp_path):
    assert snapshot.read_current(str(tmp_path))["generation"] == 0


def test_publishing_bumps_the_generation_atomically(tmp_path):
    first = snapshot.publish(str(tmp_path))
    second = snapshot.publish(str(tmp_path))
    assert first["generation"] == 1
    assert second["generation"] == 2
    assert snapshot.read_current(str(tmp_path))["generation"] == 2
    assert not os.path.exists(os.path.join(str(tmp_path), "CURRENT.tmp"))


def test_a_pin_records_the_current_generation(tmp_path):
    snapshot.publish(str(tmp_path))
    pin = snapshot.pin(str(tmp_path), name="reader-1")
    try:
        assert pin.generation == 1
        assert pin.pinned is True
        assert snapshot.pinned_generations(str(tmp_path)) == {1}
    finally:
        assert pin.release() is True
    assert snapshot.pinned_generations(str(tmp_path)) == set()


def test_a_pin_is_a_context_manager(tmp_path):
    snapshot.publish(str(tmp_path))
    with snapshot.pin(str(tmp_path)) as pin:
        assert pin.pinned
    assert not pin.pinned


def test_gc_never_removes_a_pinned_generation(tmp_path):
    """The whole point: a compaction must not pull files out from under a reader."""
    for _ in range(4):
        snapshot.publish(str(tmp_path))
    for generation in (1, 2, 3, 4):
        os.makedirs(os.path.join(str(tmp_path), f"gen-{generation}"), exist_ok=True)
        with open(os.path.join(str(tmp_path), f"gen-{generation}", "x"), "w") as h:
            h.write("x")

    pin = snapshot.pin(str(tmp_path))            # pins generation 4 (current)
    pinned = snapshot.Snapshot(index_dir=str(tmp_path), generation=1, token="old")
    os.makedirs(os.path.join(str(tmp_path), snapshot.PINS), exist_ok=True)
    with open(os.path.join(str(tmp_path), snapshot.PINS, "old"), "w") as handle:
        json.dump({"generation": 1, "name": "old-reader"}, handle)

    result = snapshot.gc(str(tmp_path), keep=0)
    assert "gen-1" not in result["removed"], "a pinned generation was deleted"
    assert "gen-4" not in result["removed"], "the current generation was deleted"
    assert "gen-2" in result["removed"] and "gen-3" in result["removed"]
    pin.release()
    pinned.release()


def test_gc_keeps_the_newest_generations_even_unpinned(tmp_path):
    for _ in range(4):
        snapshot.publish(str(tmp_path))
    for generation in (1, 2, 3, 4):
        os.makedirs(os.path.join(str(tmp_path), f"gen-{generation}"), exist_ok=True)
    result = snapshot.gc(str(tmp_path), keep=2)
    assert "gen-4" in result["kept"]
    assert "gen-3" in result["kept"]
    assert "gen-1" in result["removed"]


def test_gc_on_a_missing_directory_is_not_an_error(tmp_path):
    result = snapshot.gc(str(tmp_path / "nope"))
    assert result["removed"] == []


def test_a_compaction_publishes_a_generation(tmp_path):
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.store import ann as ann_mod
    from vrusha.store import delta
    from vrusha.store.db import Store
    import numpy as np

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    doc = store.add_document(url="https://a.test/1", title="t", text="x " * 60)
    rng = np.random.default_rng(3)
    for index in range(40):
        chunk = store.add_chunk(doc, f"chunk {index} " + "text " * 6, kind="prose")
        vector = rng.normal(size=16).astype(np.float32)
        store.set_vector(chunk, vector / np.linalg.norm(vector))
    store.commit()
    index_dir = resolve(cfg, "index")
    os.makedirs(index_dir, exist_ok=True)
    ann_mod.AnnIndex.build(store).save(index_dir)

    result = delta.safe_compact(store, index_dir, cfg)
    assert result["compacted"] is True
    assert result["generation"] == 1
    assert snapshot.read_current(index_dir)["generation"] == 1
    store.close()
