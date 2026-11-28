# Part 21 — versioned snapshots, and Phase 3 closed

**Wave 46 · `vrusha/store/snapshot.py`, `vrusha/store/delta.py`, `docs/benchmarks/`**

## What was built

- a **generation** number in a `CURRENT` file, bumped atomically after each swap
- a **pin** — a file naming the generation a reader is using
- **gc** — deletes old generation directories except the pinned ones and the
  newest `keep`
- `safe_compact()` now archives the generation it replaces, publishes a new one,
  and collects

## The rule, in one sentence

**A pinned generation is never deleted.** Before this, a reader holding an
`AnnIndex` survived a compaction because it held arrays rather than file handles —
true, but accidental, and it said nothing about the reader about to *load* during
a swap.

## The benchmark

```
pin_ms=2.29  read_current_ms=0.469  gc_ms=1.264
```

A pin costs 2.3 ms, so a reader can afford one per query; reading `CURRENT` is
0.47 ms. Report: `docs/benchmarks/11-snapshot.md`.

## What was pending

- The pin is honoured by `gc` but nothing in `query.py` takes one yet — the
  wiring is one call, and it belongs with the serve loop rather than here
- Cross-process pin expiry (a pin from a dead pid is only collected when its
  generation ages out)

## Errors found and fixed

**None in this part** — but the design was changed once while writing it: the
first sketch swapped *whole directories* per generation, which would have broken
`AnnIndex.load(index_dir)` for every existing caller. Archiving the old
generation and keeping the current files flat means nothing else had to change.

## Found while rechecking Parts 1–20

Nothing. `ruff` clean, the delta tests still pass, and the benchmark coverage
test accepted the new module because it has a benchmark of its own.

## Phase 3, closed

| part | result |
|---|---|
| 18 delta segments | **24× faster** than a rebuild |
| 19 upsert and delete | upsert = append cost; found two bugs in older code |
| 20 compaction | lock-protected, stale locks broken, 355 ms |
| 21 snapshots | pinned generations are never collected |

## What is next

**Phase 4 — the graph (Parts 22–26).** The outside review rated graph intelligence
5/10: the repository has join inference and entity resolution, but no community
detection and no multi-hop. Part 22 starts with entity extraction from prose,
which the store currently cannot do at all.

## Checklist for this part

- [x] generation bumped atomically
- [x] a pin records the generation and is releasable
- [x] gc never removes a pinned generation (tested)
- [x] gc keeps the newest generations even unpinned
- [x] a compaction publishes a generation and archives the one it replaced
- [x] benchmarked, with a report
- [ ] `query.py` takes a pin (belongs with the serve loop)
