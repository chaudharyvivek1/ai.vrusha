# The blackboard

**Benchmark:** `blackboard` · **measures:** append, delta read, replay · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `append_ms` | 1.29 |
| `delta_read_ms` | 1.66 |
| `replay_ms` | 88.70 |
| `entries` | 2,020 |

## What each number is

- append_ms: one append (write + commit)
- delta_read_ms: `since(seq)` — the read an agent makes
- replay_ms: fold 2,020 entries into state

## How to reproduce

```bash
python -m bench.registry
```

Or just this one: `python -c "from bench.registry import bench_blackboard; print(bench_blackboard())"`

## Method

Measured in-process on this pod (2 shared vCPUs, Debian 12). Latencies are
p50 of repeated runs unless the metric says otherwise. The harness is
`bench/registry.py::bench_blackboard` and it is the same code CI runs.

