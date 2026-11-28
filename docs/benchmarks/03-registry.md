# The 64-slot registry

**Benchmark:** `registry` · **measures:** register, heartbeat, sweep · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `register_ms` | 1.89 |
| `heartbeat_ms` | 0.76 |
| `sweep_ms` | 0.18 |
| `live` | 64.00 |

## What each number is

- register_ms: take the lowest free slot, atomic
- heartbeat_ms: say you are alive
- sweep_ms: mark stale agents dead

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_registry; print(bench_registry())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_registry` and it is the same code CI runs.

