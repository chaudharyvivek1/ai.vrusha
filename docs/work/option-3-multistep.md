# Option 3 — a multi-step goal, and the bug it uncovered

**Wave 70 · `vrusha/orchestrator.py`, `vrusha/compare.py`, `tests/test_orchestrator.py`**

## Diagnostic A found it in one run

The plan was to measure whether stages help. The first run reported **0 facts** for
the orchestrated path, and printing every task's state answered it immediately:

```
research Infosys   failed  attempts=2  ProgrammingError: SQLite objects created in a
                                       thread can only be used in that same thread
research Wipro     failed  attempts=2  same
research HCL       failed  attempts=2  same
compare            done    attempts=1
```

**The stage barrier was already correct.** The research tasks failed because a
`sqlite3.Connection` is bound to the thread that created it, and workers run in a
pool.

## This invalidates Part 35's orchestrated run

Part 35 compared one agent against the hierarchy and reported *identical quality*
(recall@5 0.475 both) with 42 board entries. **Every worker in that run was failing
silently**, because its worker called `retrieve()` in a thread too. The quality
number was identical for the wrong reason: it came from a separate `evaluate.run`,
not from the workers at all.

`docs/work/part-35-compare.md` and `docs/benchmarks/23-compare.md` are corrected in
this wave. **The cost measurement stands; the quality comparison did not measure
anything.**

## The fix

`run_stages(..., store_factory=...)` — one connection **per thread**, opened on first
use. The store's own docstring already said `check_same_thread=False` is for a
server and "do not use it to write from several threads at once"; a thread-local
connection respects that instead of loosening it.

## The measurement, now that it works

| | single agent | orchestrated |
|---|---|---|
| facts found | 2 of 3 | **3 of 3** |
| context the compare step received | 22,970 chars | **921 chars** |
| seconds | 0.03–0.04 | 0.24–0.36 |
| board entries | — | 6 |

**Reproduced twice with identical results** — no intermittent zero.

**This is the first measurement in the project where orchestration wins**, and the
reason is the one Part 35 could not test: the compare step gets **distilled findings,
25× smaller than the raw text**, and covers every company instead of two.

It is also **8× slower**. Both numbers are the finding.

## Release criteria, as tests

| criterion | test |
|---|---|
| three parallel researchers complete | `test_all_three_researchers_complete` |
| findings persisted | `test_every_finding_is_persisted` |
| compare starts after the barrier | `test_the_compare_task_starts_after_every_research_finding` |
| forced worker delay keeps the invariant | `test_a_forced_delay_keeps_the_ordering_invariant` |
| research failure is explicit | `test_a_failing_research_task_fails_explicitly` |
| repeated execution, no zeros | `test_repeated_execution_never_gives_a_zero_finding_run` |
| fact metric structured | the finding carries its company name |

## Errors found and fixed

**1. Thread-bound SQLite connection** — the headline; see above.
**2. My metric compared unlike with unlike** — `single_facts` counted names in 22,970
characters of raw text while `orchestrated_facts` counted them in findings truncated
to 400. Findings now carry the company name, so both sides count the same thing.
**3. A stray `threading.local()` block landed in `run_pool`** when the edit matched
both functions — a `NameError` in five tests, caught before any commit.

## What this does not do

| limitation | why |
|---|---|
| a general dependency graph | stages are linear; `A depends on B` is not expressible |
| failure recovery across stages | a failed stage 1 task leaves stage 2 with less input |
| partial-result policy | not defined; a failed research task is reported, not retried across stages |

## What is next

The reviewer's recommendation, which this wave confirms: **do not build the Goal
Execution Engine yet.** The milestone was "three agents produce reliable evidence a
fourth can depend on" — that now holds for one shape, with tests. The next step is a
second shape (a dependency graph), not a bigger engine.

## Checklist

- [x] diagnostic A run before any change; it found the cause
- [x] stage ordering verified as correct, not assumed
- [x] thread-local store; the store's own rule respected, not loosened
- [x] 3/3 facts against 2/3, 921 chars against 22,970
- [x] reproduced twice
- [x] all seven release criteria as tests
- [x] **Part 35's orchestrated quality number corrected as invalid**
