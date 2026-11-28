# Refactor — one loop instead of two

**Wave 73 · `vrusha/orchestrator.py`**

## What changed

`run_pool` was a **72-line copy** of `run_stages`. It is now a **24-line wrapper**:

```python
def run_pool(...):
    names = (list(tasks) if tasks is not None else decompose(goal))[:max_tasks]
    if not names:
        return Run(goal=goal)
    return run_stages(..., stages=[names], ...)
```

Every task in one wave is exactly what a pool with no ordering is.

## Why: the duplication produced three errors in three waves

| wave | the error |
|---|---|
| 70 | a stray `threading.local()` block landed in `run_pool` |
| 71 | a stray `skip_if` block landed in `run_pool` |
| 70 | a missing `store_factory` reference in `run_pool` |

Each was an edit that silently matched **both** functions, because their `work()`
bodies were byte-identical. A refactor removes the class of error; the fixes removed
the instances.

## Verified

- **41 orchestrator tests pass**, including every pool test
- `test_compare`, `test_remote`, `test_benchmarks` — 29 more, all pass
- the pool, orchestrator and multistep benchmarks all still run

## The false alarm, and why it mattered

The first benchmark after the refactor read **254 ms** for 10 tasks against **69.6 ms**
recorded in wave 55 — a 3.6× apparent regression, reproduced three times.

**It was the pod, not the code.** Running the *old* implementation and the *new* one
back to back on this pod:

```
old (main)        627.1 ms   (62.7 ms/task)
new (refactor)    437.7 ms   (43.8 ms/task)
old again         473.6 ms   (47.4 ms/task)
```

**Same code, 7× the time of the pod that produced the 69.6 ms figure.** The pod is
shared 2 vCPU; this has been a known risk since the first benchmark and is now
demonstrated with a direct A/B.

**The lesson:** a benchmark number is only comparable on the machine that produced it.
Every report in `docs/benchmarks/` already says "in-process, this pod" — the numbers
still need reading that way, and a refactor is not a performance change until the old
and new code are timed side by side.

## What is still duplicated

`run_remote` has its own `work()` body, close to but not identical with the others
(it talks HTTP instead of the local board). It has not produced an error yet; it is
the next candidate, and it needs the same A/B treatment before anyone claims a speed
difference.

## Checklist

- [x] `run_pool` is a wrapper, not a copy (72 → 24 lines)
- [x] all 41 orchestrator tests pass
- [x] the benchmarks still run
- [x] **the apparent regression A/B-tested against the old code on the same pod**
- [x] the pod-variance lesson written down rather than buried
