# Claims and leases

**Benchmark:** `claims` · **measures:** claim arbitration and summary · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `claim_ms` | 0.45 |
| `summary_ms` | 0.13 |

## What each number is

- claim_ms: one `INSERT … ON CONFLICT … WHERE … RETURNING`
- summary_ms: the state counts

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_claims; print(bench_claims())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_claims` and it is the same code CI runs.

