# Delta indexing

**Benchmark:** `index_delta` · **measures:** append, upsert, delete against a rebuild · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `rebuild_ms` | 495.40 |
| `append_ms` | 22.92 |
| `upsert_ms` | 17.65 |
| `delete_ms` | 21.73 |
| `ratio` | 21.62 |
| `search_ms` | 16.33 |
| `new_vectors` | 200.00 |

## What each number is

- rebuild_ms: a full `AnnIndex.build`
- append_ms / upsert_ms / delete_ms: the delta verbs
- ratio: rebuild ÷ append
- search_ms: base plus segments

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_index_delta; print(bench_index_delta())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_index_delta` and it is the same code CI runs.

