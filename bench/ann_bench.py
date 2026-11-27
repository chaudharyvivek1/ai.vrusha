"""What the index actually buys, measured against the scan it replaces.

    python bench/ann_bench.py --rows 200000 --dim 128 --shape clustered
    python bench/ann_bench.py --matrix            # every shape, one table

Two numbers per configuration, because either alone is a lie:

  latency   p50/p95 of `retrieve`'s vector signal — the exact path reads the
            whole vector table out of SQLite and multiplies, every query;
            the index routes, then reads back 128 rows for the rescore
  recall    @10 against a full float32 scan of the same corpus, over queries
            that are NOT in the index

Four shapes, because the geometry decides everything. A corpus of documents
clustered by topic is the easy case; random directions are the case where
routing cannot help, and a bench that only measures the first one is
marketing.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import ann as ann_mod
from vrusha.store.ann import AnnConfig, AnnIndex
from vrusha.store.db import Store


# ---------------------------------------------------------------------------
# data shapes
# ---------------------------------------------------------------------------


def make_shape(rows: int, dim: int, shape: str, seed: int = 11):
    """Points, plus the centres a realistic query would be drawn near."""
    rng = np.random.default_rng(seed)

    if shape == "clustered":
        clusters = max(8, rows // 500)
        centres = _unit(rng.normal(size=(clusters, dim)))
        labels = rng.integers(0, clusters, size=rows)
        points = centres[labels] + rng.normal(scale=0.12, size=(rows, dim))
        queries = centres[rng.integers(0, clusters, size=100)] + \
            rng.normal(scale=0.12, size=(100, dim))
        return _unit(points), _unit(queries), labels

    if shape == "isotropic":
        # no structure at all: every direction equally likely
        points = rng.normal(size=(rows, dim))
        queries = rng.normal(size=(100, dim))
        return _unit(points), _unit(queries), np.zeros(rows, dtype=int)

    if shape == "skewed":
        # a few huge topics and a long tail — what a crawled corpus looks like
        sizes = np.array([0.5, 0.25, 0.15, 0.06, 0.04])
        clusters = len(sizes)
        centres = _unit(rng.normal(size=(clusters, dim)))
        labels = rng.choice(clusters, size=rows, p=sizes)
        points = centres[labels] + rng.normal(scale=0.10, size=(rows, dim))
        queries = centres[rng.choice(clusters, size=100, p=sizes)] + \
            rng.normal(scale=0.10, size=(100, dim))
        return _unit(points), _unit(queries), labels

    if shape == "neardup":
        # near-identical documents: the cell holds one topic, many times
        base = _unit(rng.normal(size=(max(8, rows // 400), dim)))
        labels = rng.integers(0, base.shape[0], size=rows)
        points = base[labels] + rng.normal(scale=0.01, size=(rows, dim))
        queries = base[rng.integers(0, base.shape[0], size=100)] + \
            rng.normal(scale=0.01, size=(100, dim))
        return _unit(points), _unit(queries), labels

    raise SystemExit(f"unknown shape {shape!r}; use clustered, isotropic, skewed, neardup")


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (matrix / norms).astype(np.float32)


# ---------------------------------------------------------------------------
# a store to measure against
# ---------------------------------------------------------------------------


def build_store(root: str, points: np.ndarray) -> Store:
    """Write the corpus with executemany, not row by row.

    The benchmark measures reads, so the write path only has to be fast
    enough not to be the story. Same tables, same bytes, same pack().
    """
    from vrusha.store.embed import pack

    cfg = cfg_for(root)
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    rows, dim = points.shape
    started = time.time()
    doc = store.add_document("https://bench.test/corpus", "bench", "synthetic corpus")
    with store.transaction():
        store.conn.executemany(
            "INSERT INTO chunks (doc_id, ordinal, kind, text, chars, active) VALUES (?,?,?,?,?,1)",
            [(doc, index, "prose", f"row {index}", 6) for index in range(rows)])
        ids = [int(r[0]) for r in
               store.conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")]
        packed = [pack(points[index]) for index in range(rows)]
        store.conn.executemany(
            "INSERT INTO vectors (chunk_id, dim, dtype, blob) VALUES (?,?,?,?)",
            [(chunk_id, meta[2], meta[1], meta[0]) for chunk_id, meta in zip(ids, packed)])
        store.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('embed_dim', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(dim),))
    store.commit()
    print(f"  store        {rows:,} vectors, {dim} dims, "
          f"written in {time.time() - started:.1f}s", flush=True)
    return store


def cfg_for(root: str, **ann) -> dict:
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = root
    cfg["ann"] = {**DEFAULTS["ann"], **ann}
    return cfg


# ---------------------------------------------------------------------------
# the two paths
# ---------------------------------------------------------------------------


def exact_timings(store: Store, queries: np.ndarray, k: int, repeats: int = 3):
    """The current implementation: read the whole table, multiply, sort.

    Timed in two parts because at 200k the read dominates, and a single
    end-to-end number would hide that the cost is I/O and unpacking, not
    arithmetic. One end-to-end run on a few queries confirms the sum.
    """
    read_times = []
    for _ in range(repeats):
        started = time.perf_counter()
        ids, matrix = store.get_vectors()
        read_times.append((time.perf_counter() - started) * 1000)

    matmul = []
    for query in queries:
        started = time.perf_counter()
        scores = matrix @ query
        np.argsort(-scores)[:k]
        matmul.append((time.perf_counter() - started) * 1000)

    end_to_end = []
    for query in queries[:3]:
        started = time.perf_counter()
        got = exact_search(store, query, k)
        end_to_end.append((time.perf_counter() - started) * 1000)
    assert got
    return read_times, matmul, end_to_end


def exact_search(store: Store, query: np.ndarray, k: int, cached=None):
    if cached is None:
        ids, matrix = store.get_vectors()
    else:
        ids, matrix = cached
    scores = matrix @ query
    order = np.argsort(-scores)[:k]
    return [int(ids[i]) for i in order]


def ann_timings(index: AnnIndex, store: Store, queries: np.ndarray, k: int,
                cfg: AnnConfig, nprobe: int, truth: list):
    times, recall = [], []
    for query, expected in zip(queries, truth):
        started = time.perf_counter()
        found = index.search(query, k, store=store, cfg=cfg, nprobe=nprobe)
        times.append((time.perf_counter() - started) * 1000)
        recall.append(len(set(cid for cid, _ in found) & set(expected)) / max(1, len(expected)))
    return times, recall


def percentile(values, fraction):
    ordered = sorted(values)
    position = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[position]


def run(rows: int, dim: int, shape: str, cell_size: int, k: int = 10,
        root: str = "", rebuild: bool = False, dtype: str = "auto"):
    if root:
        root = os.path.join(root, shape)
    else:
        root = tempfile.mkdtemp(prefix=f"annbench-{shape}-")
    points, queries, _labels = make_shape(rows, dim, shape)
    print(f"\n{shape}: {rows:,} x {dim}", flush=True)
    store = Store(resolve(cfg_for(root), "db"))
    have, _max = store.vector_fingerprint()
    if have == rows and not rebuild:
        print(f"  store        reused {have:,} vectors from {root}", flush=True)
    else:
        store.close()
        store = build_store(root, points)

    cfg = cfg_for(root, target_cell_size=cell_size, rescore=128, dtype=dtype)
    started = time.time()
    index = AnnIndex.build(store, AnnConfig.from_cfg(cfg), verbose=True)
    build_seconds = time.time() - started
    index.save(ann_mod.index_dir(cfg))
    stats = index.stats()
    print(f"  index        {stats['cells']:,} cells, {stats['bytes'] / 1e6:.1f} MB, "
          f"{index.meta.get('dtype')}, built in {build_seconds:.1f}s", flush=True)

    ids, matrix = store.get_vectors()
    truth = [exact_search(store, query, k, cached=(ids, matrix)) for query in queries]
    del matrix

    sweep = []
    probes = sorted({1, 2, 4, 8, 16, 32, index.cells})
    probes = [p for p in probes if p <= index.cells]
    print(f"  {'nprobe':>7} {'p50 ms':>9} {'p95 ms':>9} {'recall@10':>10} {'speedup':>8}",
          flush=True)
    for nprobe in probes:
        times, recall = ann_timings(index, store, queries, k,
                                    AnnConfig.from_cfg(cfg), nprobe, truth)
        median = statistics.median(times)
        sweep.append({
            "nprobe": nprobe,
            "p50_ms": round(median, 2),
            "p95_ms": round(percentile(times, 0.95), 2),
            "recall_at_10": round(float(np.mean(recall)), 4),
        })

    read_ms, matmul_ms, end_to_end = exact_timings(store, queries, k)
    baseline = statistics.median(read_ms) + statistics.median(matmul_ms)
    print(f"  exact        p50 {baseline:.1f} ms  "
          f"({statistics.median(read_ms):.1f} ms reading the table + "
          f"{statistics.median(matmul_ms):.1f} ms scoring), "
          f"measured {statistics.median(end_to_end):.1f} ms end to end", flush=True)

    result = {
        "shape": shape,
        "rows": rows,
        "dim": dim,
        "cells": stats["cells"],
        "index_mb": round(stats["bytes"] / 1e6, 1),
        "dtype": index.meta.get("dtype"),
        "build_s": round(build_seconds, 1),
        "exact_ms": round(baseline, 2),
        "exact_read_ms": round(statistics.median(read_ms), 2),
        "exact_matmul_ms": round(statistics.median(matmul_ms), 2),
        "exact_end_to_end_ms": round(statistics.median(end_to_end), 2),
        "sweep": [],
    }

    for row in sweep:
        row["speedup"] = round(baseline / row["p50_ms"], 1) if row["p50_ms"] else None
        result["sweep"].append(row)
        print(f"  {row['nprobe']:>7} {row['p50_ms']:>9} {row['p95_ms']:>9} "
              f"{row['recall_at_10']:>10.1%} {row['speedup']:>7.1f}x", flush=True)

    store.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--dim", type=int, default=128)
    parser.add_argument("--shape", default="clustered",
                        choices=["clustered", "isotropic", "skewed", "neardup"])
    parser.add_argument("--cell-size", type=int, default=512)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--matrix", action="store_true",
                        help="run all four shapes and print one table")
    parser.add_argument("--out", default="", help="write the raw numbers as JSON")
    parser.add_argument("--root", default="", help="reuse a store directory")
    parser.add_argument("--dtype", default="auto", choices=["auto", "int8", "float32"])
    parser.add_argument("--rebuild", action="store_true", help="rebuild the store")
    parser.add_argument("--build-only", action="store_true",
                        help="write the corpus and stop (split a big run across calls)")
    args = parser.parse_args()

    if args.build_only:
        for shape in ([args.shape] if not args.matrix
                      else ["clustered", "isotropic", "skewed", "neardup"]):
            root = os.path.join(args.root, shape) if args.root else \
                tempfile.mkdtemp(prefix=f"annbench-{shape}-")
            points, _queries, _labels = make_shape(args.rows, args.dim, shape)
            build_store(root, points).close()
            print(f"  built {shape} in {root}", flush=True)
        return 0

    shapes = ["clustered", "isotropic", "skewed", "neardup"] if args.matrix else [args.shape]
    results = [run(args.rows, args.dim, shape, args.cell_size, args.k,
                   root=args.root, rebuild=args.rebuild, dtype=args.dtype)
              for shape in shapes]

    if args.matrix:
        print("\n\n| shape | exact p50 | nprobe | ann p50 | speedup | recall@10 |")
        print("|---|---|---|---|---|---|")
        for item in results:
            for row in item["sweep"]:
                if row["nprobe"] in (2, 4, 8, item["cells"]):
                    print(f"| {item['shape']} | {item['exact_ms']} ms | {row['nprobe']} | "
                          f"{row['p50_ms']} ms | {row['speedup']}x | "
                          f"{row['recall_at_10']:.1%} |")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2)
            handle.write("\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
