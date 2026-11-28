# Part 32 — the bounded worker pool

**Wave 56 · `vrusha/orchestrator.py`, `bench/registry.py`**

## What was built

`run_pool()` — the same loop, with tasks spread over a `ThreadPoolExecutor` of
`workers` (default 4). Every task still claims, works, records and releases; the
only difference is that the waiting overlaps.

## The measurement, and where it stops paying

| workers | wall clock | speedup |
|---|---|---|
| 1 | 238.95 ms | 1.00× |
| 2 | 107.95 ms | 2.21× |
| **4** | **64.45 ms** | **3.71×** |
| 8 | 49.40 ms | 4.84× |

8 tasks, each sleeping 20 ms.

**The curve flattens, and the flattening is the finding.** Four workers give 3.71×
— close to linear. Eight give 4.84×, not 8×, because every board write takes one
lock: the bookkeeping cannot speed up, only the waiting can. `docs/AGENTS.md` says
64 logical agents over a *small* pool, and this is the measurement that says small.

**The number to remember:** the pool is worth it while the worker waits, and worth
less as the worker gets faster. A pool of 64 over a 7 ms bookkeeping step would
spend its time queueing.

## Errors found and fixed

**None.** The pool reuses `claim`/`complete`/`release` unchanged, and those were
built for exactly this: `claim` is one statement, so two workers cannot both win.

## What it will not do

| limitation | why |
|---|---|
| speed up bookkeeping | one writer, by design |
| run more than the pool | the cap is the point; a test asserts the peak never exceeds it |
| share a task | claims prevent it, and a test would fail if two workers did the same task |
| survive a worker that hangs | no per-task timeout yet — a hung worker holds a slot until the lease expires |

## Found while rechecking Parts 1–31

Nothing. `ruff` clean, 737 tests green.

## What is next

**Part 33: remote agents.** The same protocol over HTTP, so an L4 agent can live on
another machine — which is the case the JSON server was always for.

## Checklist for this part

- [x] the pool completes every task (tested)
- [x] it never exceeds its size (tested: peak ≤ 2 with `workers=2`)
- [x] a failure does not stop the others (tested)
- [x] one report per run (tested)
- [x] every slot is released (tested)
- [x] benchmarked at 1, 2, 4 and 8 workers, with the flattening explained
- [ ] remote agents (Part 33)
