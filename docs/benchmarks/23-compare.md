# Four levels against one agent

> **Corrected in wave 70.** The orchestrated run in this benchmark **did not measure
> anything**: every worker failed with `ProgrammingError: SQLite objects created in a
> thread can only be used in that same thread`, because a SQLite connection is bound
> to the thread that made it. The "identical quality" was identical because both
> columns came from a separate `evaluate.run`, not from the workers.
>
> The **cost** measurement stands (42 board entries, 40 claims). The **quality**
> comparison is void. See `docs/benchmarks/30-multistep.md` for the measurement that
> replaced it: **3 of 3 facts against 2 of 3**, with **921 characters of context
> against 22,970**.


**Benchmark:** `compare` · **measures:** the hierarchy against a single loop, same questions · **run:** 2026-11-28

## The numbers

| metric | single | orchestrated |
|---|---|---|
| seconds | 0.32 | 0.20 |
| recall@5 | 0.475 | 0.475 |
| ndcg@10 | 0.349 | 0.349 |
| board entries | ~2 | **42** |

`cost_ratio=0.62` · `quality_delta=0.000` · `questions=40`

## The trap in these numbers

The orchestrated run looks **faster**. It is not the hierarchy — it is the 4-worker
pool, which the single loop does not use. Put the pool on the single loop and you
get the same speed with **2 board entries instead of 42**.

Reporting "orchestration is 1.6x faster" would be the most misleading true sentence
in this repository.

## What this measures, and what it cannot

**Measures:** the cost of the hierarchy — 42 entries, 40 claims, up to 8 slots, for
**zero quality change**, because both runs call the same `retrieve()`.

**Cannot measure:** the benefit. This corpus has no multi-step goal — no task where
step 2 needs step 1's output — and that is the only case a hierarchy is for.

## Method

Both runs on the 40-question set, same embedder, same retrieval. Harness:
`bench/registry.py::bench_compare`.
