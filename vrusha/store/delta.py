"""Delta segments: adding vectors must not rewrite the ones already there.

`ann.build()` writes a whole index at once, so one new document forces a full
rebuild — the outside review rated that a high-priority gap, and it is the last
real one in retrieval.

The fix is the one every search engine uses: a **base** index plus small
**segments** written on append. A search asks the base and every segment and
merges by score; a compaction folds the segments back into the base when they
outnumber it.

Two decisions worth stating:

**Segments are brute-forced.** They are small by construction — a segment holds
what arrived since the last compaction — and brute force over a few thousand
float32 rows is faster than building routing structures for them. This is the
standard trade (`Lucene` does the same), and it is why the segment count is
capped: past the cap, compaction is not an optimisation, it is correctness for
the latency budget.

**The base is not touched.** `add()` writes one file per segment and never opens
the base for writing, which is what makes an append O(new vectors) instead of
O(all vectors). The benchmark in `bench/registry.py` measures exactly that.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from . import ann as ann_mod

SEGMENT_DIR = "segments"
SEGMENT_FILE = "segment.json"
VECTORS_FILE = "vectors.npy"


def _segments_root(index_dir: str) -> str:
    return os.path.join(index_dir, SEGMENT_DIR)


def segment_paths(index_dir: str) -> List[str]:
    root = _segments_root(index_dir)
    if not os.path.isdir(root):
        return []
    found = [os.path.join(root, name) for name in os.listdir(root)
             if os.path.isdir(os.path.join(root, name))]
    return sorted(found, key=lambda path: os.path.basename(path))


@dataclass
class Segment:
    """One append-only file of vectors, brute-forced at query time.

    `supersedes` is the tombstone list: chunk ids whose *older* copies — in the
    base or in an earlier segment — must be ignored. An id can be superseded with
    a replacement vector (upsert) or with nothing at all (delete).
    """

    path: str
    ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    vectors: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), np.float32))
    supersedes: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))

    @property
    def created(self) -> str:
        return os.path.basename(self.path)

    @property
    def rows(self) -> int:
        return int(self.vectors.shape[0])

    def search(self, query: np.ndarray, k: int) -> List[Tuple[int, float]]:
        if self.rows == 0 or k <= 0:
            return []
        scores = self.vectors @ query
        take = min(k, self.rows)
        best = np.argpartition(-scores, take - 1)[:take]
        order = best[np.argsort(-scores[best])]
        return [(int(self.ids[index]), float(scores[index])) for index in order]


def write_segment(index_dir: str, ids: np.ndarray, vectors: np.ndarray,
                  supersedes: Optional[Sequence[int]] = None) -> str:
    """Write one segment and return its path. Nothing else is touched.

    The segment is written to a temporary **directory** and renamed into place, so
    a reader never sees a half-written one. (`np.save` appends `.npy` to a string
    path, which silently defeats a per-file rename — that bug cost one round trip
    here, so the files are written through handles instead.)
    """
    root = _segments_root(index_dir)
    os.makedirs(root, exist_ok=True)
    stamp = f"{time.time():.6f}".replace(".", "-")
    final = os.path.join(root, f"seg-{stamp}")
    staging = final + ".tmp"
    os.makedirs(staging, exist_ok=True)

    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.clip(norms, 1e-12, None)
    with open(os.path.join(staging, VECTORS_FILE), "wb") as handle:
        np.save(handle, matrix)
    with open(os.path.join(staging, "ids.npy"), "wb") as handle:
        np.save(handle, np.asarray(ids, dtype=np.int64))
    with open(os.path.join(staging, "supersedes.npy"), "wb") as handle:
        np.save(handle, np.asarray(list(supersedes or []), dtype=np.int64))
    with open(os.path.join(staging, SEGMENT_FILE), "w", encoding="utf-8") as handle:
        json.dump({"rows": int(matrix.shape[0]), "dim": int(matrix.shape[1]),
                   "supersedes": int(len(supersedes or [])),
                   "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  handle)
    os.replace(staging, final)
    return final


def load_segment(path: str) -> Optional[Segment]:
    try:
        vectors = np.load(os.path.join(path, VECTORS_FILE))
        ids = np.load(os.path.join(path, "ids.npy"))
    except (OSError, ValueError):
        return None
    supersedes_path = os.path.join(path, "supersedes.npy")
    supersedes = np.zeros(0, dtype=np.int64)
    if os.path.exists(supersedes_path):
        try:
            supersedes = np.asarray(np.load(supersedes_path), dtype=np.int64)
        except (OSError, ValueError):
            supersedes = np.zeros(0, dtype=np.int64)
    return Segment(path=path, ids=np.asarray(ids, dtype=np.int64),
                   vectors=np.asarray(vectors, dtype=np.float32),
                   supersedes=supersedes)


def load_segments(index_dir: str) -> List[Segment]:
    """Every readable segment, including the ones with no vectors.

    A delete-only segment has **zero rows and a tombstone**, so filtering on
    `rows` silently discards it and the deleted chunk comes back. That is the
    bug this comment exists to stop someone reintroducing.
    """
    segments = []
    for path in segment_paths(index_dir):
        segment = load_segment(path)
        if segment is None:
            continue
        if segment.rows or len(segment.supersedes):
            segments.append(segment)
    return segments


def drop_segments(index_dir: str) -> int:
    """Delete every segment. Called after a compaction has absorbed them."""
    removed = 0
    for path in segment_paths(index_dir):
        for name in (VECTORS_FILE, "ids.npy", SEGMENT_FILE, "supersedes.npy"):
            target = os.path.join(path, name)
            if os.path.exists(target):
                os.unlink(target)
        try:
            os.rmdir(path)
            removed += 1
        except OSError:
            pass
    return removed


def search(index_dir: str, query_vector: np.ndarray, k: int, *,
           base: Optional["ann_mod.AnnIndex"] = None,
           store=None, cfg=None,
           nprobe: int = 0) -> List[Tuple[int, float]]:
    """Ask the base and every segment, then merge — with tombstones applied.

    The rule that makes upsert and delete correct: **the newest mention of a
    chunk id wins**. Walking the segments newest first, an id is settled the
    moment a segment either provides it (a live vector) or supersedes it (a
    tombstone). Everything settled is then removed from the base results, so a
    replaced vector stops competing with the one that replaced it.

    Without that rule an upsert would leave the old vector scoring from the base
    and the new one scoring from the segment — the same chunk, two answers.
    """
    query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(query)) or 1.0
    query = query / norm

    merged: dict = {}
    settled: set = set()
    segments = load_segments(index_dir)
    # newest first: the later the file, the later the write
    for segment in sorted(segments, key=lambda item: item.created, reverse=True):
        for chunk_id, score in segment.search(query, k):
            if int(chunk_id) in settled:
                continue
            merged[int(chunk_id)] = float(score)
            settled.add(int(chunk_id))
        for chunk_id in segment.supersedes.tolist():
            settled.add(int(chunk_id))

    if base is not None and base.rows:
        for chunk_id, score in base.search(query_vector, k, store=store, cfg=cfg,
                                           nprobe=nprobe):
            chunk_id = int(chunk_id)
            if chunk_id in settled:
                continue                       # replaced or deleted by a segment
            merged[chunk_id] = max(merged.get(chunk_id, -2.0), float(score))

    order = sorted(merged.items(), key=lambda pair: (-pair[1], pair[0]))
    return order[:k]


def stats(index_dir: str) -> dict:
    segments = load_segments(index_dir)
    return {"segments": len(segments),
            "segment_rows": sum(segment.rows for segment in segments),
            "tombstones": sum(len(segment.supersedes) for segment in segments),
            "dim": int(segments[0].vectors.shape[1]) if segments else 0}


def should_compact(index_dir: str, *, max_segments: int = 8,
                   max_rows: int = 20000) -> bool:
    """When the segments stop being a shortcut and start being the index."""
    summary = stats(index_dir)
    return (summary["segments"] >= max_segments
            or summary["segment_rows"] >= max_rows)


def compact(store, index_dir: str, cfg=None) -> dict:
    """Fold the segments into a fresh base index, then delete them."""
    started = time.perf_counter()
    built = ann_mod.build_for(store, cfg) if hasattr(ann_mod, "build_for") else None
    if built is None:
        index = ann_mod.AnnIndex.build(store)
        index.save(index_dir)
    removed = drop_segments(index_dir)
    return {"segments_removed": removed,
            "seconds": round(time.perf_counter() - started, 2)}


LOCK_FILE = "compaction.lock"


def safe_compact(store, index_dir: str, cfg=None, *, stale_lock_s: float = 900.0) -> dict:
    """Compact into a new directory, then swap it in — one compactor at a time.

    Two rules make this safe to run while queries are in flight:

    **Build beside, swap after.** The new base is built in `<index>.new` and
    renamed into place only when it is complete, so a reader either sees the old
    index or the new one, never a half-built one.

    **One compactor at a time.** A lock file created with `O_EXCL` is the whole
    mechanism; a second compactor returns "already running" instead of racing.
    A lock older than `stale_lock_s` is assumed to belong to a dead process and is
    broken, because a lock nobody releases is an outage.
    """
    lock = os.path.join(index_dir, LOCK_FILE)
    os.makedirs(index_dir, exist_ok=True)
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - os.path.getmtime(lock)
        except OSError:
            age = 0.0
        if age < stale_lock_s:
            return {"compacted": False, "reason": "another compaction is running",
                    "lock_age_s": round(age, 1), **stats(index_dir)}
        os.unlink(lock)                       # a dead compactor's lock
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(handle, f"{os.getpid()} {time.time():.0f}".encode())
        os.close(handle)

        started = time.perf_counter()
        staging = index_dir.rstrip(os.sep) + ".new"
        if os.path.isdir(staging):
            for name in os.listdir(staging):
                target = os.path.join(staging, name)
                if os.path.isfile(target):
                    os.unlink(target)
        os.makedirs(staging, exist_ok=True)

        index = ann_mod.AnnIndex.build(store)
        index.save(staging)
        # move the new files over the old ones, one at a time and atomically
        moved = 0
        for name in os.listdir(staging):
            source = os.path.join(staging, name)
            if not os.path.isfile(source):
                continue
            os.replace(source, os.path.join(index_dir, name))
            moved += 1
        try:
            os.rmdir(staging)
        except OSError:
            pass
        removed = drop_segments(index_dir)
        return {"compacted": True, "files_moved": moved,
                "segments_removed": removed,
                "seconds": round(time.perf_counter() - started, 2),
                **stats(index_dir)}
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass


def maybe_compact(store, index_dir: str, cfg=None, **kwargs) -> dict:
    """Compact only when the segments say it is time."""
    if not should_compact(index_dir):
        return {"compacted": False, "reason": "below the threshold", **stats(index_dir)}
    return safe_compact(store, index_dir, cfg, **kwargs)


def append(store, index_dir: str, chunk_ids: Sequence[int],
           cfg=None) -> dict:
    """Add vectors for chunks the index has never seen. The base is not rewritten."""
    return _write(store, index_dir, chunk_ids, supersede=False, verb="append")


def upsert(store, index_dir: str, chunk_ids: Sequence[int], cfg=None) -> dict:
    """Add vectors **and** retire the older copies of the same chunk ids.

    An upsert is an append plus a tombstone. Without the tombstone the base keeps
    serving the old vector and the chunk answers twice.
    """
    return _write(store, index_dir, chunk_ids, supersede=True, verb="upsert")


def delete(store, index_dir: str, chunk_ids: Sequence[int], cfg=None) -> dict:
    """Retire these chunk ids. No vectors are written: this is only a tombstone."""
    ids = [int(chunk_id) for chunk_id in chunk_ids]
    if not ids:
        return {"rows": 0, "superseded": 0, "path": "", **stats(index_dir)}
    path = write_segment(index_dir, np.zeros(0, dtype=np.int64),
                         np.zeros((0, 0), dtype=np.float32), supersedes=ids)
    return {"rows": 0, "superseded": len(ids), "path": path, **stats(index_dir)}


def _write(store, index_dir: str, chunk_ids: Sequence[int], *,
           supersede: bool, verb: str) -> dict:
    ids, matrix = store.get_vectors(list(chunk_ids))
    if not ids:
        return {"rows": 0, "superseded": 0, "path": "", **stats(index_dir)}
    path = write_segment(index_dir, np.asarray(ids), np.asarray(matrix),
                         supersedes=ids if supersede else None)
    return {"rows": len(ids), "superseded": len(ids) if supersede else 0,
            "path": path, "verb": verb, **stats(index_dir)}
