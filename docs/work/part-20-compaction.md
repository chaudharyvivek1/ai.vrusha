# Part 20 — compaction, and a lock that can be broken

**Wave 45 · `vrusha/store/delta.py`, `bench/registry.py`, `docs/benchmarks/`**

## What was built

- **`safe_compact()`** — builds the new base in `<index>.new`, moves the files
  over one at a time (each move atomic), drops the segments, releases the lock
- **a lock file** created with `O_EXCL` — one compactor at a time
- **a stale lock is broken**, not waited on: a lock older than `stale_lock_s`
  belongs to a dead process, and a lock nobody releases is an outage
- **`maybe_compact()`** — compacts only when `should_compact()` says so
- **`docs/benchmarks/`** — ten reports, one per subsystem, generated from the
  harness

## The benchmark

```
rebuild_ms=350.01  append_ms=21.04  upsert_ms=24.35  delete_ms=19.03
compact_ms=354.56  ratio=16.64  search_ms=4.53  compacted=1.0
```

Compaction costs what a rebuild costs (355 ms vs 350 ms) — that is the point: it
is the expensive operation, and the design's job is to make it rare. The delta
verbs stay at ~20 ms, **16.6× cheaper**, which is what makes deferring it worth
doing.

## Two rules, and why each exists

**Build beside, swap after.** A reader sees the old index or the new one, never a
half-built one. Files are moved individually and atomically, so the worst case is
a mix of old and new *files* for a moment, never a truncated one.

**One compactor at a time.** Two compactions would fight over the same files. The
lock is `O_EXCL`, so the second one returns *"another compaction is running"*
rather than corrupting the index.

## Errors found and fixed

**Nothing broke — and that is worth recording.** The lock is released in a
`finally`, and a test deliberately breaks the builder (`disk full`) to prove the
lock is not left behind. Another test ages a lock by 5,000 seconds and asserts it
is broken rather than honoured.

## Found while rechecking Parts 1–19

**The `retrieval` benchmark did not record which embedder ran.** On this pod
model2vec is not installed, so the pipeline fell back to the deterministic hash —
and `recall@5` read **0.47** where the same set scores **0.70** with model2vec.
Same code, same corpus, a third of the quality, and nothing in the output said
which. The benchmark now reports `model2vec=1|0`, and
`docs/benchmarks/06-retrieval.md` explains what the flag means.

That is the sharpest version yet of this project's oldest rule: **a number without
a method is not a number.**

## What is next

**Part 21: versioned snapshots.** Compaction is safe for a *new* reader, but a
reader holding an `AnnIndex` object keeps the old one in memory — which works, but
is accidental rather than designed. The next part makes the snapshot explicit.

## Checklist for this part

- [x] compaction builds beside and swaps in
- [x] one compactor at a time, by lock
- [x] a stale lock is broken
- [x] the lock is released even when the build fails
- [x] queries still answer after a compaction
- [x] benchmarked: compaction = rebuild cost, delta verbs 16.6× cheaper
- [x] ten benchmark reports in `docs/benchmarks/`
- [x] the retrieval benchmark records its embedder
