# The orchestration loop

**Benchmark:** `orchestrator` · **measures:** the loop's own overhead per task · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `tasks` | 10 |
| `ms` | 69.6 |
| `ms_per_task` | 6.97 |
| `done` | 10 |

## What each number is

- `ms_per_task`: the loop's bookkeeping — register, claim, append, complete, release —
  with a worker that does nothing
- `done`: must be 10. It read **0** on the first run, which is how the bug below was found

## The bug this benchmark found

`run_id` was `f"run-{int(time.perf_counter())}"` — **colliding for every run in the
same second**. The second run found the first one's claims already `done`, was
refused, and marked every task failed.

The unit tests never saw it because each passes an explicit `run_id="r1"`. **A test
suite that supplies its own ids cannot catch an id collision** — that is what a
benchmark harness is for.

## Method

In-process, this pod, worker returns a constant. Harness:
`bench/registry.py::bench_orchestrator`.
