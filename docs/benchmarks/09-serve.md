# The JSON API

**Benchmark:** `serve` · **measures:** one request through the socket · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `request_ms` | 8.67 |
| `max_ms` | 16.17 |

## What each number is

- request_ms: POST /search, p50
- max_ms: the worst of 20

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_serve; print(bench_serve())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_serve` and it is the same code CI runs.

