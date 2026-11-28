"""What the address ranking cost, and what it costs now.

`_address_ranking` parsed every address string and called `semantic_similarity`
once per chunk. At 200k chunks that was 64 ms of an 82 ms query — the largest
remaining cost in the retriever, and the one `docs/BENCHMARKS.md` named.

This measures the two implementations on the same store: the Python loop that
was there before (kept in `_reference`) against the cached integer matrix and
numpy that replaced it.
"""
from __future__ import annotations

import os
import random
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import query as query_module
from vrusha.store.address import Address, centroid_table, semantic_similarity
from vrusha.store.db import Store


def build(chunks: int) -> Store:
    root = tempfile.mkdtemp(prefix="addr-bench-")
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = root
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    doc = store.add_document(url="https://bench.test/corpus", title="bench",
                             text="bench " * 100)

    rng = random.Random(7)
    with store.transaction():
        for index in range(chunks):
            chunk = store.add_chunk(doc, f"chunk {index} " + "text " * 12,
                                    kind="prose")
            store.set_address(chunk, Address((
                1 + index % 2,                       # two domains
                1 + index % 7,                       # seven families
                1 + index % 3,                       # three kinds
                1 + rng.randrange(40),               # forty clusters
                rng.randrange(0, 900),               # ordinals inside a cluster
            )))
    store.commit()
    return store


def reference(store: Store, hint: Address, limit: int = 60):
    """The implementation that was replaced, verbatim in shape."""
    centroids = centroid_table(store.get_centroids())
    scored = []
    for chunk_id, address in store.addresses():
        score = semantic_similarity(hint, address, centroids)
        if score > 0:
            scored.append((chunk_id, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:limit]


def timeit(function, repeats: int = 5):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples), max(samples)


def main() -> int:
    chunks = int(os.environ.get("ADDRESS_BENCH_CHUNKS", "20000"))
    store = build(chunks)
    stored = store.conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE address IS NOT NULL").fetchone()[0]
    print(f"corpus   {stored:,} addressed chunk(s)\n")

    hint = Address.parse("1.2.3.1.100")
    query_module._ADDRESS_CACHE.clear()

    before = timeit(lambda: reference(store, hint))
    query_module._address_ranking(store, hint, 60)          # warm the matrix
    after = timeit(lambda: query_module._address_ranking(store, hint, 60))

    same = reference(store, hint) == query_module._address_ranking(store, hint, 60)

    print(f"{'implementation':<34}{'p50 ms':>10}{'max ms':>10}")
    print("-" * 54)
    print(f"{'python loop (before)':<34}{before[0]:>10.1f}{before[1]:>10.1f}")
    print(f"{'numpy + cache (after)':<34}{after[0]:>10.2f}{after[1]:>10.2f}")
    print()
    print(f"speedup   {before[0] / max(after[0], 1e-9):.0f}x")
    print(f"identical results: {same}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
