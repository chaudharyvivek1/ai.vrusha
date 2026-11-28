# The address ranking

**Benchmark:** `address` · **measures:** the ranking, warm · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `warm_ms` | 0.54 |
| `rows` | 2,000.00 |

## What each number is

- warm_ms: score 2,000 addressed chunks in numpy

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_address; print(bench_address())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_address` and it is the same code CI runs.

