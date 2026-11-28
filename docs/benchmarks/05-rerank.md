# The reranker

**Benchmark:** `rerank` · **measures:** the second stage over 20 candidates · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `noop_ms` | 0.00 |
| `lexical_ms` | 1.89 |
| `candidates` | 20.00 |

## What each number is

- noop_ms: the seam doing nothing
- lexical_ms: field-weighted BM25 over 20 hits

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_rerank; print(bench_rerank())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_rerank` and it is the same code CI runs.

