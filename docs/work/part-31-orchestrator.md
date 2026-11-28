# Part 31 — the orchestration loop

**Wave 55 · `vrusha/orchestrator.py`, `bench/registry.py`**

## What was built

The loop that uses the pieces in order:

```
L1/super   registers, records the plan on the board
L2/admin   registers, decomposes, reports at the end
L3/worker  registers, claims a task, runs it, appends the finding, releases
L4         the report entry: "N done, M failed of T"
```

```
goal: fetch sources then extract tables then write report
  3 done, 0 failed of 3 in 0.06s, 5 board entries
  ok   fetch sources        did fetch sources
  ok   extract tables       did extract tables
  ok   write report         did write report
```

## Three rules

**The worker is injected.** `run(..., worker=callable)` means the loop is tested
without a model, a network or a query — and the same loop drives a fake worker in a
test and real retrieval in production.

**A failing task is retried once, then marked failed.** Retrying forever hides a
broken worker; not retrying wastes a run on one flaky call. `MAX_ATTEMPTS = 2`, and
both paths are tested.

**Every task ends in a state** — done or failed, on the board and in the claims — so
a run that dies leaves a record of where it died rather than a silent gap.

## The benchmark

```
tasks=10  ms=69.6  ms_per_task=6.97  done=10
```

The loop's own overhead is **7 ms per task** with a worker that does nothing —
against a 9 ms retrieval, so the bookkeeping is comparable to the work at this
scale, and the obvious next optimisation is batching the board writes.

## The bug the benchmark found

**`run_id` was `f"run-{int(time.perf_counter())}"` — which collides for every run in
the same second.** The second run found the first one's claims already `done`, was
refused the claim, and marked every task failed. The unit tests never saw it because
each test uses an explicit `run_id="r1"`.

The benchmark ran the loop four times in a row and reported `done=0` for a worker
that always succeeds. **A test suite that passes its own ids cannot catch an id
collision** — that is what a benchmark harness is for.

## What it will not do

| limitation | why |
|---|---|
| decompose a goal that needs judgement | splits on `then`/`;`/newlines, otherwise one task |
| run tasks in parallel | sequential; parallelism needs the worker pool from `docs/AGENTS.md` §4 |
| plan adaptively from results | a fixed task list, no re-planning |
| survive a crash mid-run | the board records it; nothing resumes it |

## Found while rechecking Parts 1–30

Nothing. `ruff` clean, 723 tests green.

## What is next

**Part 32: the worker pool.** The loop is sequential, and `docs/AGENTS.md` says 64
logical agents over a bounded pool. The next part bounds the pool and measures
whether concurrency helps or just contends on the board's single writer.

## Checklist for this part

- [x] plan, claim, work, report, in that order (tested)
- [x] every task ends in a state, on the board and in the claims (tested)
- [x] slots are released even when a task fails (tested)
- [x] one retry, then failed — both paths tested
- [x] an explicit task list beats decomposition (tested)
- [x] the task cap is enforced (tested)
- [x] benchmarked: 7 ms per task of loop overhead
- [x] the run-id collision found and fixed
- [ ] a bounded worker pool (Part 32)
