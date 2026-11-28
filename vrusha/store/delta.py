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
    """One append-only file of vectors, brute-forced at query time."""

    path: str
    ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    vectors: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), np.float32))

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


def write_segment(index_dir: str, ids: np.ndarray, vectors: np.ndarray) -> str:
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
    with open(os.path.join(staging, SEGMENT_FILE), "w", encoding="utf-8") as handle:
        json.dump({"rows": int(matrix.shape[0]), "dim": int(matrix.shape[1]),
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
    return Segment(path=path, ids=np.asarray(ids, dtype=np.int64),
                   vectors=np.asarray(vectors, dtype=np.float32))


def load_segments(index_dir: str) -> List[Segment]:
    segments = []
    for path in segment_paths(index_dir):
        segment = load_segment(path)
        if segment is not None and segment.rows:
            segments.append(segment)
    return segments


def drop_segments(index_dir: str) -> int:
    """Delete every segment. Called after a compaction has absorbed them."""
    removed = 0
    for path in segment_paths(index_dir):
        for name in (VECTORS_FILE, "ids.npy", SEGMENT_FILE):
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
    """Ask the base and every segment, then merge.

    A segment result and a base result are both cosine in [-1, 1], so they can be
    merged by score without calibration — the same property that lets the router
    fuse engines.
    """
    query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(query)) or 1.0
    query = query / norm

    merged: dict = {}
    if base is not None and base.rows:
        for chunk_id, score in base.search(query_vector, k, store=store, cfg=cfg,
                                           nprobe=nprobe):
            merged[int(chunk_id)] = max(merged.get(int(chunk_id), -2.0), float(score))
    for segment in load_segments(index_dir):
        for chunk_id, score in segment.search(query, k):
            merged[chunk_id] = max(merged.get(chunk_id, -2.0), score)

    order = sorted(merged.items(), key=lambda pair: (-pair[1], pair[0]))
    return order[:k]


def stats(index_dir: str) -> dict:
    segments = load_segments(index_dir)
    return {"segments": len(segments),
            "segment_rows": sum(segment.rows for segment in segments),
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


def append(store, index_dir: str, chunk_ids: Sequence[int],
           cfg=None) -> dict:
    """Add vectors for these chunks to a new segment. The base is not rewritten."""
    ids, matrix = store.get_vectors(list(chunk_ids))
    if not ids:
        return {"rows": 0, "path": ""}
    path = write_segment(index_dir, np.asarray(ids), np.asarray(matrix))
    return {"rows": len(ids), "path": path, **stats(index_dir)}
