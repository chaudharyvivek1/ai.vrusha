# Part 30 — anticipation, and Phase 5 closed

**Wave 54 · `vrusha/store/anticipate.py`, `bench/registry.py`**

## What was built

- **`predict()`** — open `question` entries on the board, then unclaimed tasks
- **`warm()`** — retrieves for each prediction while a **time budget** lasts, and
  caches the result
- **`lookup()`** — a warm hit if there is one, otherwise retrieve and cache
- **a bounded cache** — 32 entries, 60-second TTL, oldest evicted first

## The design is about being wrong

Anticipation is a bet. Every part of it is capped:

| cap | what it prevents |
|---|---|
| **3 tasks** per prediction | a wrong guess wastes bounded work, never a run |
| **500 ms** budget | a slow prefetch cannot delay the real query |
| **32 entries, 60 s TTL** | a stale prefetch is worse than none — it looks like a warm hit |

A test sets `budget_ms=0.0` and asserts that **nothing** is warmed and everything
is skipped: the budget is a hard stop, not a target.

## The benchmark

```
put_ms=0.0028  hit_ms=0.0008  miss_ms=0.0006
budget_ms=500.0  max_tasks=3.0
```

A cache hit costs **0.8 µs** against a ~9 ms retrieval — so the saving is the whole
query, and the bookkeeping is free.

## Errors found and fixed

**`since_of_kind()` called `self._entry(row)`, and the helper is a module-level
`_row()`.** The method raised `AttributeError`, `predict()` swallowed it in its
`except`, and prediction silently returned nothing. Found by calling the method
directly rather than through the predictor — the swallowing is deliberate (a
prediction is optional and must never break a run), but it also hid the bug, which
is the cost of that choice and worth writing down.

## What it will not do

| limitation | why |
|---|---|
| learn which tasks come next | the prediction is questions and unclaimed tasks; anything cleverer needs measuring first |
| prefetch *content* rather than a query result | the cache holds retrieval results, not chunks |
| keep a warm entry across a compaction | the TTL is 60 s; a generation change invalidates nothing explicitly |

## Found while rechecking Parts 1–29

Nothing else. `ruff` clean, 711 tests green.

## Phase 5, closed

| part | result |
|---|---|
| 27 compaction | 85% threshold; declines when it would grow the text |
| 28 verifiable summaries | `seq` range recorded; `verify()` catches a dropped decision |
| 29 offload | 25,000 tokens → a 15-token reference in 3.7 ms |
| 30 anticipation | 3 tasks, 500 ms, 32 entries, 60 s |

**Together: 7,579 tokens of run history became 1,749, a 25,000-token page became a
pointer, and the next query costs 0.8 µs instead of 9 ms when the guess was right.**

## What is next

**Phase 6 — orchestration (Parts 31–35).** The pieces exist: board, claims, registry,
compaction, offload, anticipation. What does not exist is the loop that ties them
together — L1 plan, L2 decompose, L3 work, L4 report — and the measurement that says
whether four levels beat one agent with a good retrieval layer.

## Checklist for this part

- [x] the cache is capped, expiring and counts hits and misses (tested)
- [x] prediction prefers open questions, then unclaimed tasks (tested)
- [x] the budget is a hard stop (tested: `budget_ms=0` warms nothing)
- [x] a bad task does not stop the others (tested)
- [x] a warm hit avoids retrieval (tested)
- [x] benchmarked: hit 0.8 µs against ~9 ms
- [ ] the orchestration loop (Part 31)
