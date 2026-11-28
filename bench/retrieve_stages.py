"""Where the 80 ms goes now that the address ranking is 2.5 ms.

`docs/BENCHMARKS.md` reported `retrieve()` at 82.6 ms p50, of which the address
ranking was ~64 ms. Wave 22 took that to ~2.5 ms, so the question this script
answers is the obvious next one: **what is the other 80%?**

It times each stage of one query separately — embed, bm25, vector, address,
fusion, hydration — over a real store built from this repository's documentation,
and reports the p50 of each. Timing a pipeline by summing its stages is always a
little wrong (the stages interleave with the allocator), so the total is measured
separately and printed beside the sum.
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from typing import Callable, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.harvest.fetch import Document
from vrusha.ingest import ingest_documents
from vrusha.languages import load_registry
from vrusha.rules import load_rules
from vrusha.store import query as query_module
from vrusha.store.db import Store
from vrusha.store.embed import get_embedder

QUERIES = [
    "how do I stop the crawler from hammering a site",
    "why are my results wrong after I added new pages",
    "what happens when two pieces of text get the same number",
    "how do I know a page is allowed to be kept",
    "finding equations inside a research paper",
    "answering a question and showing where it came from",
    "what do the numbers in the tree mean",
    "making search faster on a cheap machine",
]


def build_store(verbose: bool = True) -> Store:
    root = tempfile.mkdtemp(prefix="retrieve-bench-")
    cfg = _cfg(root)
    store = Store(resolve(cfg, "db"))

    documents = []
    for name in sorted(os.listdir(os.path.join(ROOT, "docs"))):
        if not name.endswith(".md"):
            continue
        path = os.path.join(ROOT, "docs", name)
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        documents.append(Document(url=f"file://{path}", title=name,
                                  text=body, status=200, kind="prose"))
    report = ingest_documents(documents, store, cfg, load_rules(), load_registry(),
                              get_embedder(cfg), licence="MIT", verbose=False)
    if verbose:
        print(f"store    {report.chunks} chunk(s) from {len(documents)} document(s)\n")
    return store, cfg


def _cfg(root: str) -> dict:
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = root
    cfg["store"] = {**DEFAULTS["store"]}
    ensure_dirs(cfg)
    return cfg


def timeit(function: Callable[[], object], repeats: int = 15) -> Dict[str, float]:
    samples: List[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000.0)
    return {"p50": statistics.median(samples),
            "p95": sorted(samples)[int(len(samples) * 0.95) - 1]}


def main() -> int:
    store, cfg = build_store()
    embedder = get_embedder(cfg)
    rules, registry = load_rules(), load_registry()

    # warm every cache the way a server would after its first query
    query_module.retrieve(store, embedder, QUERIES[0], cfg)

    stages: Dict[str, List[float]] = {
        "embed": [], "bm25": [], "vector": [], "address": [],
        "fusion+hydrate": [], "total": [],
    }
    candidates = int(cfg.get("store", {}).get("candidates", 60) or 60)

    for query in QUERIES:
        for _ in range(6):
            stages["embed"].append(timeit(lambda: embedder.encode_one(query), 1)["p50"])
            stages["bm25"].append(
                timeit(lambda: store.fts_search(query, limit=candidates), 1)["p50"])
            stages["vector"].append(
                timeit(lambda: query_module._vector_ranking(
                    store, embedder, query, candidates, cfg=cfg), 1)["p50"])

            # the hint is whatever `retrieve` derives for this query
            hint = getattr(query_module, "_hint_for", lambda *a, **k: None)(
                store, query, cfg)
            if hint is not None:
                stages["address"].append(
                    timeit(lambda: query_module._address_ranking(
                        store, hint, candidates), 1)["p50"])

            stages["total"].append(
                timeit(lambda: query_module.retrieve(store, embedder, query, cfg), 1)["p50"])

    total = statistics.median(stages["total"])
    parts = {name: statistics.median(values) if values else 0.0
             for name, values in stages.items() if name != "total"}
    accounted = sum(parts.values())
    stages["fusion+hydrate"] = [max(0.0, total - accounted)]

    print(f"{'stage':<18}{'p50 ms':>9}{'share':>8}")
    print("-" * 35)
    for name, value in parts.items():
        print(f"{name:<18}{value:>9.2f}{value / total:>7.0%}")
    print(f"{'unattributed':<18}{max(0.0, total - accounted):>9.2f}"
          f"{max(0.0, total - accounted) / total:>7.0%}")
    print("-" * 35)
    print(f"{'retrieve() total':<18}{total:>9.2f}{1.0:>7.0%}")
    print()
    print(f"chunks in store: {store.conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]:,}")
    print(f"p95 of the total: {sorted(stages['total'])[int(len(stages['total']) * 0.95) - 1]:.2f} ms")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
