"""Head to head with RagForge's published numbers, on their own corpus geometry.

RagForge's clustered generator (`bench/shapes.ts` in that repo):

    clusters = max(8, round(rows / 400))
    noise    = 0.5 / sqrt(dim)
    vector   = normalise(centre[cluster] + gaussian * noise)

This reproduces exactly that, so the corpus is the one their table was measured
on, and reports the columns they report: p50, p95, qps, recall@10, rows scored.

Why a shim store and not SQLite: this measures the *index*, and both engines'
published numbers are taken with the vectors already in memory. Writing 200k
rows through the store first would measure SQLite, not the thing being
compared. The integrated path is measured by `bench/ann_bench.py`.

    python bench/headtohead.py float32
    python bench/headtohead.py int8
"""
from __future__ import annotations

import os
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vrusha.store.ann import AnnConfig, AnnIndex

# RagForge's own published rows, rescore=128, same corpus. rows -> (p50, p95, recall)
RAGFORGE = {
    "int8": {6436: (7.57, 15.02, 0.980), 12675: (11.83, 16.99, 0.985),
             25014: (18.60, 25.76, 0.997)},
    "float32": {6436: (5.49, 9.16, 0.980), 12675: (8.90, 16.28, 0.985),
                25014: (13.51, 18.46, 0.997)},
}


def normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (matrix / norms).astype(np.float32)


def ragrforge_clustered(rows: int, dim: int, seed: int = 7):
    rng = np.random.default_rng(seed)
    clusters = max(8, round(rows / 400))
    centres = normalise(rng.normal(size=(clusters, dim)))
    noise = 0.5 / np.sqrt(dim)
    labels = rng.integers(0, clusters, size=rows)
    points = normalise(centres[labels] + rng.normal(scale=noise, size=(rows, dim)))
    queries = normalise(centres[rng.integers(0, clusters, size=100)]
                        + rng.normal(scale=noise, size=(100, dim)))
    return points, queries, clusters


class Shim:
    """The two store methods the index needs, answered from memory."""

    def __init__(self, points: np.ndarray):
        self.points = points
        self.ids = np.arange(1, points.shape[0] + 1, dtype=np.int32)
        self.row_of = {int(cid): i for i, cid in enumerate(self.ids)}

    def vector_fingerprint(self):
        return int(self.points.shape[0]), int(self.ids[-1])

    def embed_dim(self):
        return int(self.points.shape[1])

    def iter_vectors(self, batch: int = 8192, after: int = 0):
        for start in range(0, self.points.shape[0], batch):
            ids = self.ids[start:start + batch].tolist()
            if ids and ids[-1] <= after:
                continue
            yield ids, self.points[start:start + batch]

    def get_vectors(self, chunk_ids=None):
        if chunk_ids is None:
            return self.ids.tolist(), self.points
        rows = [self.row_of[int(cid)] for cid in chunk_ids if int(cid) in self.row_of]
        return [int(self.ids[r]) for r in rows], self.points[rows]


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def main() -> int:
    rows, dim, k = 200_000, 128, 10
    dtype = sys.argv[1] if len(sys.argv) > 1 else "auto"

    points, queries, clusters = ragrforge_clustered(rows, dim)
    print(f"corpus  {rows:,} x {dim}, {clusters} clusters, noise 0.5/sqrt(dim), "
          f"dtype {dtype}", flush=True)
    store = Shim(points)

    started = time.perf_counter()
    index = AnnIndex.build(store, AnnConfig(target_cell_size=512, rescore=128,
                                            dtype=dtype))
    build_s = time.perf_counter() - started
    sizes = np.diff(index.ptr)
    print(f"index   {index.cells:,} cells, median {int(np.median(sizes))} rows/cell, "
          f"{index.meta['dtype']}, built in {build_s:.1f}s", flush=True)

    truth = [set(np.argsort(-(points @ query))[:k].tolist()) for query in queries]

    cfg = AnnConfig(target_cell_size=512, rescore=128, dtype=dtype)
    print(f"\n{'nprobe':>6} {'rows':>8} {'p50 ms':>8} {'p95 ms':>8} {'qps':>6} "
          f"{'recall@10':>10} {'ragforge p50, recall':>22}", flush=True)
    for nprobe in (2, 4, 8, 12, 16, 32, index.cells):
        if nprobe > index.cells:
            continue
        times, recalls = [], []
        for query, expected in zip(queries, truth):
            started = time.perf_counter()
            found = index.search(query, k, store=store, cfg=cfg, nprobe=nprobe)
            times.append((time.perf_counter() - started) * 1000)
            recalls.append(len({cid - 1 for cid, _ in found} & expected) / len(expected))

        p50 = statistics.median(times)
        scored = int(sum(sizes[c] for c in range(min(nprobe, index.cells))))
        # their rows-scored never lands on ours exactly: match within 10%
        theirs = None
        for their_rows, figures in RAGFORGE[index.meta["dtype"]].items():
            if abs(their_rows - scored) <= 0.10 * max(their_rows, scored):
                theirs = figures
                break
        reference = f"{theirs[0]:.2f} ms, {theirs[2]:.1%}" if theirs else "—"
        print(f"{nprobe:>6} {scored:>8,} {p50:>8.2f} {percentile(times, 0.95):>8.2f} "
              f"{1000 / p50:>6.0f} {np.mean(recalls):>9.1%} {reference:>22}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
