# Ingestion

**Benchmark:** `ingest` · **measures:** documents to chunks and vectors · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `docs_per_second` | 6.13 |
| `chunks` | 255.00 |
| `ms_per_doc` | 163.02 |

## What each number is

- docs_per_second: whole documents per second
- ms_per_doc: per document
- chunks: how many came out

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_ingest; print(bench_ingest())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_ingest` and it is the same code CI runs.

