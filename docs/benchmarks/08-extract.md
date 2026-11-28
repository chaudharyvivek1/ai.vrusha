# Extraction

**Benchmark:** `extract` · **measures:** fixtures to typed tables · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `files_per_second` | 23.94 |
| `tables` | 4.00 |
| `ms_per_file` | 41.77 |

## What each number is

- files_per_second: fixture files per second
- ms_per_file: per file
- tables: tables found

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_extract; print(bench_extract())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_extract` and it is the same code CI runs.

