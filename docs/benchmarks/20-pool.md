# The worker pool

**Benchmark:** `pool` · **measures:** 1, 2, 4 and 8 workers on the same 8 tasks · **run:** 2026-11-28

## The numbers

| workers | wall clock | speedup |
|---|---|---|
| 1 | 238.95 ms | 1.00× |
| 2 | 107.95 ms | 2.21× |
| **4** | **64.45 ms** | **3.71×** |
| 8 | 49.40 ms | 4.84× |

## What each number is

8 tasks, each sleeping 20 ms — so the measurement is about *overlap*, not CPU.

## The finding: the curve flattens

Four workers give **3.71×**, close to linear. Eight give **4.84×, not 8×**, because
every blackboard write takes one lock: the bookkeeping cannot speed up, only the
waiting can.

`docs/AGENTS.md` says 64 logical agents over a *small* pool. This is the measurement
that says small: **the pool is worth it while the worker waits, and worth less as the
worker gets faster.** A pool of 64 over a 7 ms bookkeeping step would spend its time
queueing.

## Method

In-process, this pod, `ThreadPoolExecutor`. Harness:
`bench/registry.py::bench_pool`.
