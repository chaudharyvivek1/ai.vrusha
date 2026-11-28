# Retrieval, end to end

**Benchmark:** `retrieval` · **measures:** a whole query, and its quality · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `query_ms` | 9.45 |
| `recall@5` | 0.47 |
| `ndcg@10` | 0.35 |
| `questions` | 40.00 |

## What each number is

- query_ms: one question through the pipeline
- recall@5 / ndcg@10: quality on the 40-question set
- model2vec: 1 if the semantic embedder ran, 0 if it fell back to hash

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_retrieval; print(bench_retrieval())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_retrieval` and it is the same code CI runs.

## The number that needs its context

`model2vec = 0`. If that is **0**, the semantic
embedder was not installed and the pipeline fell back to the deterministic
hash — which measures the index, not the retrieval. With model2vec the same
set scores recall@5 **0.70** and nDCG@10 **0.58**; with the hash fallback,
0.47 and 0.35. A benchmark that did not record this would be a number
without a method.

