# Snapshots

**Benchmark:** `snapshot` · **measures:** pin, read and collect index generations · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `pin_ms` | 2.290 |
| `read_current_ms` | 0.469 |
| `gc_ms` | 1.264 |

## What each number is

- pin_ms: create a pin and release it
- read_current_ms: read the CURRENT file
- gc_ms: collect generations with keep=2

## Why it is this cheap

A pin costs 2.3 ms, which is why a reader can afford one per query. `read_current`
is 0.47 ms — it is one small file read.

## How to reproduce

```bash
python -m bench.registry
```

## Method

In-process, this pod (2 shared vCPUs). p50 of repeated runs. Harness:
`bench/registry.py::bench_snapshot`.

