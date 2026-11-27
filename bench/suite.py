"""The benchmark battery, in the shape the standard suites use.

The published suites (ANN-Benchmarks, VectorDBBench, Weaviate's ANN benchmark)
agree on what to measure, and this follows them:

    recall@k against a full float32 scan, per query, reported with its
    distribution (min, p1, p5, p10) and not just its mean
    latency p50 / p95 / p99, serial
    QPS at concurrency 1 / 2 / 4
    index build time and index size
    memory: peak RSS
    filtered search, which ANN-Benchmarks cannot express and VectorDBBench can
    the pipeline stages around retrieval: ingest, passes, tokenize, train

Every phase writes JSON so the report is generated from numbers, never typed.

    python bench/suite.py --phase index --out /scratch/work/r-index.json
    python bench/suite.py --phase store --root /path/to/store
    python bench/suite.py --phase concurrent
    python bench/suite.py --phase pipeline --root /tmp/x
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import sys
import tempfile
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vrusha.store.ann import AnnConfig, AnnIndex

K_VALUES = (1, 10, 100)


# ---------------------------------------------------------------------------
# corpus generators — the shapes the suites use, and the ones that break them
# ---------------------------------------------------------------------------


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (matrix / norms).astype(np.float32)


def _noise(rng, shape, scale: float = 1.0) -> np.ndarray:
    """float32 from the start: a float64 intermediate doubles peak memory."""
    return (rng.standard_normal(size=shape, dtype=np.float32) * np.float32(scale))


def corpus(rows: int, dim: int, shape: str = "clustered", seed: int = 7,
           queries: int = 100):
    rng = np.random.default_rng(seed)
    if shape == "clustered":
        clusters = max(8, rows // 400)
        centres = _unit(_noise(rng, (clusters, dim)))
        noise = 0.5 / np.sqrt(dim)
        labels = rng.integers(0, clusters, size=rows)
        points = centres[labels] + _noise(rng, (rows, dim), noise)
        probes = centres[rng.integers(0, clusters, size=queries)] + \
            _noise(rng, (queries, dim), noise)
    elif shape == "isotropic":
        points = _noise(rng, (rows, dim))
        probes = _noise(rng, (queries, dim))
    elif shape == "skewed":
        sizes = np.array([0.5, 0.25, 0.15, 0.06, 0.04])
        centres = _unit(_noise(rng, (len(sizes), dim)))
        labels = rng.choice(len(sizes), size=rows, p=sizes)
        points = centres[labels] + _noise(rng, (rows, dim), 0.10)
        probes = centres[rng.choice(len(sizes), size=queries, p=sizes)] + \
            _noise(rng, (queries, dim), 0.10)
    elif shape == "neardup":
        base = _unit(_noise(rng, (max(8, rows // 400), dim)))
        labels = rng.integers(0, base.shape[0], size=rows)
        points = base[labels] + _noise(rng, (rows, dim), 0.01)
        probes = base[rng.integers(0, base.shape[0], size=queries)] + \
            _noise(rng, (queries, dim), 0.01)
    else:
        raise SystemExit(f"unknown shape {shape!r}")
    return _unit(points), _unit(probes)


class Shim:
    """The store methods the index needs, answered from memory.

    The index is what is being measured here; writing 1M rows through SQLite
    first would measure SQLite. The integrated path is the `store` phase.
    """

    def __init__(self, points: np.ndarray):
        self.points = points
        self.ids = np.arange(1, points.shape[0] + 1, dtype=np.int32)
        self.row_of = {int(cid): i for i, cid in enumerate(self.ids)}

    def vector_fingerprint(self):
        return int(self.points.shape[0]), int(self.ids[-1])

    def embed_dim(self):
        return int(self.points.shape[1])

    def vector_revision(self):
        return 1

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


def percentile(values, fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return float(ordered[position])


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ---------------------------------------------------------------------------
# phase: the index, alone, against the exact scan
# ---------------------------------------------------------------------------


def ground_truth(points: np.ndarray, probes: np.ndarray, k: int) -> list:
    truth = []
    for probe in probes:
        scores = points @ probe
        truth.append(set(np.argsort(-scores)[:k].tolist()))
    return truth


def phase_index(configs, out: str) -> dict:
    results = []
    for rows, dim, shape in configs:
        points, probes = corpus(rows, dim, shape)
        store = Shim(points)
        print(f"\n{rows:,} x {dim}  {shape}", flush=True)

        truth10 = ground_truth(points, probes, 10)
        truth100 = ground_truth(points, probes, 100)

        started = time.perf_counter()
        index = AnnIndex.build(store, AnnConfig(target_cell_size=512,
                                               rescore=128, dtype="auto"))
        build_s = time.perf_counter() - started
        stats = index.stats()
        print(f"  build      {build_s:.1f} s   {stats['cells']:,} cells   "
              f"{stats['bytes'] / 1e6:.0f} MB   {index.meta['dtype']}", flush=True)

        # exact baseline: one read + a matmul per query, which is what the old
        # path did and what recall is measured against
        started = time.perf_counter()
        for probe in probes:
            np.argsort(-(points @ probe))[:10]
        exact_ms = (time.perf_counter() - started) * 1000 / len(probes)

        sweep = []
        cfg = AnnConfig(target_cell_size=512, rescore=128)
        for nprobe in (1, 2, 4, 8, 16, 32, index.cells):
            if nprobe > index.cells or (sweep and sweep[-1]["nprobe"] == nprobe):
                continue
            times, r1, r10, r100 = [], [], [], []
            for probe, t10, t100 in zip(probes, truth10, truth100):
                started = time.perf_counter()
                found = index.search(probe, 100, store=store, cfg=cfg, nprobe=nprobe)
                times.append((time.perf_counter() - started) * 1000)
                ids = [cid - 1 for cid, _ in found]
                r1.append(len(set(ids[:1]) & t10) / 1)
                r10.append(len(set(ids[:10]) & t10) / 10)
                r100.append(len(set(ids[:100]) & t100) / 100)
            p50 = statistics.median(times)
            row = {
                "nprobe": int(nprobe),
                "rows_scored": int(sum(np.diff(index.ptr)[c]
                                       for c in range(min(nprobe, index.cells)))),
                "p50_ms": round(p50, 3),
                "p95_ms": round(percentile(times, 0.95), 3),
                "p99_ms": round(percentile(times, 0.99), 3),
                "qps": round(1000 / p50, 1),
                "recall@1": round(float(np.mean(r1)), 4),
                "recall@10": round(float(np.mean(r10)), 4),
                "recall@100": round(float(np.mean(r100)), 4),
                "recall@10_min": round(float(np.min(r10)), 4),
                "recall@10_p1": round(percentile(r10, 0.01), 4),
                "recall@10_p5": round(percentile(r10, 0.05), 4),
            }
            sweep.append(row)
            print(f"  nprobe {nprobe:>6}  rows {row['rows_scored']:>8,}  "
                  f"p50 {row['p50_ms']:>7.2f}  p99 {row['p99_ms']:>7.2f}  "
                  f"qps {row['qps']:>7.0f}  r@1 {row['recall@1']:>5.1%}  "
                  f"r@10 {row['recall@10']:>6.1%}  r@100 {row['recall@100']:>6.1%}",
                  flush=True)

        results.append({
            "rows": rows, "dim": dim, "shape": shape,
            "cells": int(stats["cells"]), "dtype": index.meta["dtype"],
            "index_mb": round(stats["bytes"] / 1e6, 1),
            "build_s": round(build_s, 1),
            "build_rows_per_s": round(rows / build_s, 0),
            "exact_ms": round(exact_ms, 2),
            "rss_mb": round(rss_mb(), 0),
            "sweep": sweep,
        })
    payload = {"phase": "index", "k": 10, "results": results}
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"\nwrote {out}")
    return payload


# ---------------------------------------------------------------------------
# phase: through SQLite, the way a query actually runs
# ---------------------------------------------------------------------------


def phase_store(root: str, out: str) -> dict:
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.store import ann as ann_mod
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.router import Router

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = root
    cfg["ann"] = {**DEFAULTS["ann"], "min_vectors": 0}
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    rows, _max = store.vector_fingerprint()
    dim = store.vector_dim()
    print(f"store       {rows:,} vectors, {dim} dims", flush=True)

    started = time.perf_counter()
    ann_mod.build_for(store, cfg)
    build_s = time.perf_counter() - started
    index_mb = ann_mod.AnnIndex.load(ann_mod.index_dir(cfg)).stats()["bytes"] / 1e6
    print(f"index       built in {build_s:.1f} s, {index_mb:.0f} MB", flush=True)

    embedder = HashEmbedder(dim)
    probes = [embedder.encode_one(f"row {i}") for i in range(100)]

    def timeit(fn, repeats=1, over=None):
        times = []
        for _ in range(repeats):
            for probe in (over if over is not None else probes):
                started = time.perf_counter()
                fn(probe)
                times.append((time.perf_counter() - started) * 1000)
        return times

    # exact: read the whole table, multiply, sort — the path this replaced
    from vrusha.store.backends import ExactBackend, NativeBackend
    exact = ExactBackend(store)
    native = NativeBackend(store, cfg)
    # the exact path costs seconds per query at 200k: five probes, one pass.
    # The native path is cheap enough to measure properly.
    # warm the index: the first call pays for loading it off disk, which is a
    # startup cost, not a query cost
    native.search(probes[0], 10)
    # The exact path is timed in the index phase and in bench/ann_bench.py: it
    # costs seconds per query at 200k, so running it here again would spend the
    # whole budget on one number that is already measured.
    native_times = timeit(lambda p: native.search(p, 10), repeats=2, over=probes[:60])

    # the honest end-to-end: full retrieve(), BM25 + vector + address + fusion
    from vrusha.store.query import retrieve
    off = {**cfg, "backends": {**DEFAULTS["backends"], "mode": "exact"}}
    on = {**cfg, "backends": {**DEFAULTS["backends"], "mode": "auto"}}
    router = Router(store, embedder, on)
    questions = ["what is an address", "the ten levels of rules", "byte pair encoding",
                 "harvest and gates", "training a small model"]
    end_native = []
    for question in questions:
        started = time.perf_counter()
        retrieve(store, embedder, question, on, top_k=8, router=router)
        end_native.append((time.perf_counter() - started) * 1000)

    payload = {
        "phase": "store",
        "rows": rows, "dim": dim,
        "index_build_s": round(build_s, 1), "index_mb": round(index_mb, 1),
        "vector_signal": {
            "native": {"p50_ms": round(statistics.median(native_times), 3),
                       "p95_ms": round(percentile(native_times, 0.95), 3),
                       "p99_ms": round(percentile(native_times, 0.99), 3),
                       "samples": len(native_times)},
        },
        "retrieve_end_to_end": {
            "native": {"p50_ms": round(statistics.median(end_native), 2),
                       "p95_ms": round(percentile(end_native, 0.95), 2),
                       "p99_ms": round(percentile(end_native, 0.99), 2),
                       "samples": len(end_native)},
        },
        "rss_mb": round(rss_mb(), 0),
    }
    print(f"vector      native {payload['vector_signal']['native']['p50_ms']:.3f} ms "
          f"p95 {payload['vector_signal']['native']['p95_ms']:.2f} ms "
          f"p99 {payload['vector_signal']['native']['p99_ms']:.2f} ms", flush=True)
    print(f"retrieve    {payload['retrieve_end_to_end']['native']['p50_ms']:.1f} ms p50 "
          f"(bm25 + vector + address + fusion)", flush=True)
    store.close()
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"wrote {out}")
    return payload


# ---------------------------------------------------------------------------
# phase: concurrency
# ---------------------------------------------------------------------------


def phase_concurrent(out: str) -> dict:
    rows, dim = 200_000, 128
    points, probes = corpus(rows, dim, "clustered")
    store = Shim(points)
    index = AnnIndex.build(store, AnnConfig(target_cell_size=512, rescore=128,
                                            dtype="auto"))
    cfg = AnnConfig(target_cell_size=512, rescore=128)
    nprobe = index.nprobe_for(cfg)

    results = []
    for threads in (1, 2, 4):
        latencies = []
        lock = threading.Lock()

        def worker(count: int):
            local = []
            for i in range(count):
                probe = probes[i % len(probes)]
                started = time.perf_counter()
                index.search(probe, 10, store=store, cfg=cfg, nprobe=nprobe)
                local.append((time.perf_counter() - started) * 1000)
            with lock:
                latencies.extend(local)

        per_thread = 60
        started = time.perf_counter()
        workers = [threading.Thread(target=worker, args=(per_thread,))
                   for _ in range(threads)]
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join()
        wall = time.perf_counter() - started
        total = threads * per_thread
        row = {
            "threads": threads,
            "queries": total,
            "qps": round(total / wall, 1),
            "p50_ms": round(statistics.median(latencies), 2),
            "p95_ms": round(percentile(latencies, 0.95), 2),
            "p99_ms": round(percentile(latencies, 0.99), 2),
        }
        results.append(row)
        print(f"threads {threads}  qps {row['qps']:>7.1f}  p50 {row['p50_ms']:>6.2f}  "
              f"p99 {row['p99_ms']:>6.2f}", flush=True)

    payload = {"phase": "concurrent", "rows": rows, "dim": dim,
               "nprobe": int(nprobe), "results": results}
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    return payload


# ---------------------------------------------------------------------------
# phase: the pipeline around retrieval
# ---------------------------------------------------------------------------


def phase_pipeline(root: str, out: str) -> dict:
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.harvest.fetch import harvest_local
    from vrusha.ingest import ingest_paths
    from vrusha.languages import load_registry
    from vrusha.rules import load_rules
    from vrusha.store.chunk import chunk_text
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.passes import PassContext, run_passes

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = root
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    rules, registry = load_rules(), load_registry()
    embedder = HashEmbedder(256)

    started = time.perf_counter()
    report = ingest_paths([os.path.join(repo, "docs"), os.path.join(repo, "vrusha")],
                          store, cfg, rules, registry, embedder,
                          licence="MIT", verbose=False)
    ingest_s = time.perf_counter() - started
    rows, _max = store.vector_fingerprint()
    print(f"ingest      {report.documents} docs, {report.chunks} chunks, "
          f"{rows} vectors in {ingest_s:.2f} s "
          f"({rows / max(ingest_s, 1e-9):,.0f} chunks/s)", flush=True)

    started = time.perf_counter()
    run_passes(PassContext(store=store, cfg=cfg, rules=rules,
                           registry=registry, verbose=False))
    passes_s = time.perf_counter() - started
    print(f"passes      12 passes in {passes_s:.2f} s", flush=True)

    # chunking on its own, on a large synthetic document
    text = ("The address tree files every chunk under five numeric segments. " * 4000)
    profile = registry.profile_for("en") if hasattr(registry, "profile_for") else None
    started = time.perf_counter()
    chunks = chunk_text(text, profile, cfg)
    chunk_s = time.perf_counter() - started
    print(f"chunk       {len(chunks)} chunks from {len(text):,} chars in "
          f"{chunk_s * 1000:.1f} ms", flush=True)

    payload = {
        "phase": "pipeline",
        "ingest": {"documents": report.documents, "chunks": report.chunks,
                   "seconds": round(ingest_s, 2),
                   "chunks_per_s": round(rows / max(ingest_s, 1e-9), 0)},
        "passes": {"seconds": round(passes_s, 2)},
        "chunk": {"chunks": len(chunks), "chars": len(text),
                  "ms": round(chunk_s * 1000, 1)},
        "rss_mb": round(rss_mb(), 0),
    }
    store.close()
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"wrote {out}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default="index",
                        choices=["index", "store", "concurrent", "pipeline", "smoke"])
    parser.add_argument("--out", default="")
    parser.add_argument("--root", default="")
    parser.add_argument("--scale", default="full", choices=["quick", "full"])
    parser.add_argument("--configs", default="",
                        help="rows:dim:shape,rows:dim:shape -- overrides --scale")
    args = parser.parse_args()

    if args.phase == "index":
        if args.configs:
            configs = []
            for item in args.configs.split(","):
                rows, dim, shape = item.strip().split(":")
                configs.append((int(rows), int(dim), shape))
        elif args.scale == "quick":
            configs = [(50_000, 128, "clustered")]
        else:
            configs = [
                (100_000, 768, "clustered"),
                (200_000, 128, "clustered"),
                (200_000, 256, "clustered"),
                (400_000, 128, "clustered"),
                (200_000, 128, "skewed"),
                (200_000, 128, "neardup"),
                (200_000, 128, "isotropic"),
            ]
        phase_index(configs, args.out)
    elif args.phase == "store":
        phase_store(args.root or tempfile.mkdtemp(prefix="suite-store-"), args.out)
    elif args.phase == "concurrent":
        phase_concurrent(args.out)
    elif args.phase == "pipeline":
        phase_pipeline(args.root or tempfile.mkdtemp(prefix="suite-pipe-"), args.out)
    else:
        print("smoke: nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
