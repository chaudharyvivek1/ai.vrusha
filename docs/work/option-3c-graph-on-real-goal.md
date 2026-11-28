# Option 3c — the graph on the real goal

**Wave 72 · `vrusha/compare.py`, `tests/test_compare.py`**

## What was measured

The company comparison, run **both ways**: two linear stages, and the same plan as a
dependency graph where `compare` waits on all three researchers.

| shape | facts | context | board entries | seconds |
|---|---|---|---|---|
| stages | 3/3 | 921 | 6 | 0.70 / 2.06 |
| **graph** | **3/3** | **921** | **6** | 0.71 / 1.09 |

**They agree exactly.** The graph is a generalisation that does not change behaviour
on a linear plan — which is the correctness check it needed, and the reason to write
the general one at all.

The seconds vary by 3× between runs of the *same* shape: that is pod noise, not a
difference, and reporting it as a speedup would be dishonest.

## The bug this measurement exposed: reusing a `run_id` poisons the board

The first attempt ran each shape twice with the same `run_id`, and the numbers moved
in the wrong direction:

```
stages  facts=3/3 context=921 entries=6 s=1.99
graph   facts=3/3 context=921 entries=6 s=1.74
stages  facts=3/3 context=0   entries=8 s=0.15   ← same run_id
graph   facts=3/3 context=0   entries=9 s=0.12   ← same run_id
```

A second run with the same id finds its tasks **already claimed and done**, so
nothing runs; the facts still read 3/3 because the *first* run's findings are still
on the board, and the entry count goes up because the board keeps both runs.

**This is the wave 55 run-id collision again, in a new place** — and this time it
made a measurement look *better*, which is the dangerous direction. A test now pins
the behaviour: `test_a_reused_run_id_poisons_the_board`.

## Errors found and fixed

**None in the code.** The `shape` parameter, the dispatch and the refusal for an
unknown shape all worked first time. The error was in how I measured.

## What this settles

| | |
|---|---|
| the graph runs a real goal | yes — 3/3 facts, same as stages |
| it changes the result | no, and it should not |
| it changes the speed | **not measurably** — the variation is pod noise |
| what it buys | expressiveness: `D waits for B and C` is now writable |

## What is next

**Merge `run_pool` and `run_stages`.** Their `work()` bodies are identical, and that
duplication has now produced three errors in three waves (a stray `threading.local()`
block, a stray `skip_if` block, and a missing `store_factory`). It is the highest
return-on-effort item left.

## Checklist

- [x] the real goal run as a graph (3/3 facts, 921 chars, 6 entries)
- [x] the same goal run as stages, and the two compared
- [x] an unknown shape refused with the list (tested)
- [x] **the reused-run-id hazard pinned by a test**
- [x] pod noise named as pod noise rather than reported as a speedup
