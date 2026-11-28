"""The index, on real embedding geometry instead of synthetic shapes.

Every recall table in `docs/BENCHMARKS.md` came from `bench/suite.py`, which
generates vectors: clustered, isotropic, skewed, near-duplicate. Those shapes are
useful — they are the adversarial cases a vendor would rather not publish — but
they are guesses about what a real embedder produces.

This replaces the guess with the thing itself: every text file in the repository,
chunked, embedded with `model2vec`, and the same index measured against the same
brute-force reference. The question it answers is the one the synthetic rows only
approximated: **on real embeddings, does IVF routing still find what the scan
finds, and what does it cost?**

It also reports the geometry, because that is what explains the answer: if real
embeddings are tightly clustered, routing is easy; if they are nearly isotropic,
no routing table can help and the numbers say so.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from typing import Dict, List, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import ann as ann_mod
from vrusha.store.ann import AnnConfig, AnnIndex

CHUNK = 500
MIN_CHUNK = 200
SKIP_DIRS = {".git", "__pycache__", "build", "dist", ".pytest_cache", "data"}


# ---------------------------------------------------------------------------
# corpus: the repository's own text
# ---------------------------------------------------------------------------


def collect_text(limit_files: int = 400) -> List[Tuple[str, str]]:
    """(path, chunk) for every text file, chunked at 500 characters."""
    pieces: List[Tuple[str, str]] = []
    extensions = {".md", ".py", ".txt", ".json", ".toml", ".yaml", ".yml", ".cff"}
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() not in extensions:
                continue
            path = os.path.join(base, name)
            try:
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    body = handle.read()
            except OSError:
                continue
            for start in range(0, len(body), CHUNK):
                piece = body[start:start + CHUNK].strip()
                if len(piece) >= MIN_CHUNK:
                    pieces.append((os.path.relpath(path, ROOT), piece))
            if len(pieces) >= limit_files * 20:
                return pieces
    return pieces


# ---------------------------------------------------------------------------
# a shim store, so the index can be built without SQLite
# ---------------------------------------------------------------------------


class Shim:
    """The three methods `AnnIndex` needs. No database, no store."""

    def __init__(self, matrix: np.ndarray):
        self.matrix = np.asarray(matrix, dtype=np.float32)
        self.ids = np.arange(1, self.matrix.shape[0] + 1, dtype=np.int32)
        self.row_of = {int(cid): index for index, cid in enumerate(self.ids)}

    def vector_dim(self):
        return int(self.matrix.shape[1])

    def embed_dim(self):
        return int(self.matrix.shape[1])

    def vector_fingerprint(self):
        return (int(self.matrix.shape[0]), int(self.ids[-1]))

    def vector_revision(self):
        return 1

    def iter_vectors(self, batch: int = 4096, after: int = 0):
        for start in range(0, self.matrix.shape[0], batch):
            rows = self.matrix[start:start + batch]
            yield self.ids[start:start + batch], rows

    def get_vectors(self, chunk_ids=None):
        if chunk_ids is None:
            return list(self.ids), self.matrix
        rows = [self.row_of[int(cid)] for cid in chunk_ids if int(cid) in self.row_of]
        return [int(self.ids[row]) for row in rows], self.matrix[rows]


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


def geometry(matrix: np.ndarray, samples: int = 2000, seed: int = 5) -> Dict[str, float]:
    """What the vectors look like, which is what explains the recall curve.

    `mean_cosine` near 0 means the corpus is nearly isotropic and no routing
    table can help; high means tight topics, which is where IVF pays.
    """
    rng = np.random.default_rng(seed)
    count = matrix.shape[0]
    take = min(samples, count)
    rows = matrix[rng.choice(count, size=take, replace=False)]
    rows = rows / np.clip(np.linalg.norm(rows, axis=1, keepdims=True), 1e-12, None)
    similarity = rows @ rows.T
    upper = similarity[np.triu_indices(take, k=1)]
    nearest = np.sort(similarity, axis=1)[:, -2]      # best neighbour, not itself
    return {"mean_cosine": float(upper.mean()),
            "p95_cosine": float(np.percentile(upper, 95)),
            "mean_nearest": float(nearest.mean()),
            "rows": int(count), "dim": int(matrix.shape[1])}


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------


def main() -> int:
    started = time.perf_counter()
    pieces = collect_text()
    print(f"corpus   {len(pieces):,} chunks from the repository, "
          f"{sum(len(text) for _path, text in pieces):,} characters")

    try:
        from model2vec import StaticModel
    except Exception as exc:                     # noqa: BLE001
        print(f"model2vec is required for this measurement: {exc}")
        return 2
    model = StaticModel.from_pretrained("minishlab/potion-base-8M")
    texts = [text for _path, text in pieces]
    embed_started = time.perf_counter()
    matrix = np.asarray(model.encode(texts), dtype=np.float32)
    embed_seconds = time.perf_counter() - embed_started
    matrix /= np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12, None)
    print(f"embedded in {embed_seconds:.1f}s with model2vec "
          f"({matrix.shape[0]:,} x {matrix.shape[1]})\n")

    stats = geometry(matrix)
    print(f"geometry mean cosine {stats['mean_cosine']:.3f} "
          f"(p95 {stats['p95_cosine']:.3f}), "
          f"mean nearest-neighbour {stats['mean_nearest']:.3f}")
    print("  a mean near 0 would mean near-isotropic: no routing table helps\n")

    store = Shim(matrix)
    build_started = time.perf_counter()
    index = AnnIndex.build(store, AnnConfig(target_cell_size=128, rescore=128))
    build_seconds = time.perf_counter() - build_started
    print(f"index    {index.cells:,} cells, median "
          f"{int(np.median(np.diff(index.ptr)))} rows/cell, "
          f"built in {build_seconds:.1f}s, "
          f"{index.stats()['bytes'] / 1e6:.1f} MB\n")

    rng = np.random.default_rng(9)
    queries = matrix[rng.choice(matrix.shape[0], size=100, replace=False)]
    exact = []
    for query in queries:
        scores = matrix @ query
        exact.append(list(np.argsort(-scores)[:10]))

    header = f"{'nprobe':>7}{'rows':>9}{'p50 ms':>9}{'p95 ms':>9}{'recall@10':>11}"
    print(header)
    print("-" * len(header))
    for nprobe in (1, 2, 4, 8, 16, 32, index.cells):
        times, recalls = [], []
        # rows actually scored: the cells opened, times their median size
        rows_scored = min(nprobe, index.cells) * int(
            np.median(np.diff(index.ptr)))
        for query, truth in zip(queries, exact):
            begin = time.perf_counter()
            hits = index.search(query, 10, cfg=AnnConfig(target_cell_size=128,
                                                        rescore=128),
                                nprobe=nprobe)
            times.append((time.perf_counter() - begin) * 1000)
            # hits carry chunk ids (1-based); truth is row indices (0-based)
            found = {int(cid) - 1 for cid, _score in hits}
            recalls.append(len(found & set(truth)) / 10)
        p50 = statistics.median(times)
        p95 = sorted(times)[int(len(times) * 0.95) - 1]
        label = f"{nprobe}" if nprobe != index.cells else f"{nprobe} (all)"
        print(f"{label:>7}{rows_scored:>9,}{p50:>9.2f}{p95:>9.2f}"
              f"{statistics.mean(recalls):>10.0%}")

    print()
    print("recall@10 is against the full float32 scan of the same matrix.")
    print(f"total wall clock {time.perf_counter() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
