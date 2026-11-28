# Part 18 — delta segments: an append must not rewrite the index

**Wave 43 · `vrusha/store/delta.py`, `bench/registry.py`**

## What was built

A **base** index plus append-only **segments**:

- `append(store, index_dir, ids)` — writes one small segment, **never opens the
  base for writing**
- `search(index_dir, query, k, base=…)` — asks the base and every segment, merges
  by cosine; both are cosine in [-1, 1], so they merge without calibration
- `should_compact()` / `compact()` — folds the segments into a fresh base and
  deletes them
- a segment is written to a temporary **directory** and renamed, so a reader never
  sees half of one

## The benchmark, and why it is the point

```
rebuild_ms=383.38  append_ms=15.93  ratio=24.06  search_ms=8.20  new_vectors=200
```

Adding 200 vectors to a 4,000-vector index: **15.9 ms against 383 ms — 24×
faster**, and the base files are not touched (a test asserts their mtimes are
unchanged). Search with one segment attached costs 8.2 ms.

That is the whole claim of the part, and it is a number in the same table as
everything else rather than a sentence in a design document.

## What was pending

- Wiring `vrusha index append` into the CLI, and automatic compaction (Part 20)
- Upsert and delete by chunk id (Part 19) — today an append only adds
- Versioned snapshots (Part 21)

## Errors found and fixed

**1. `np.save` silently appends `.npy` to a string path.** The segment was written
to `vectors.npy.tmp`, numpy saved `vectors.npy.tmp.npy`, and the rename failed
with `FileNotFoundError`. Fixed by writing the whole segment into a temporary
*directory* and renaming that — which is also more atomic than renaming three
files one at a time.

**2. A corrupt segment had to be survivable.** A half-written file must not take
the search down, so `load_segment` returns `None` on a bad read and the search
carries on with the base. There is a test that corrupts a segment's bytes and
asserts the base still answers.

## The design trade, stated

**Segments are brute-forced.** They are small by construction — what arrived since
the last compaction — and brute force over a few thousand float32 rows beats
building routing structures for them. That is the standard trade (Lucene does the
same), and it is why the segment count is capped: past the cap, compaction stops
being an optimisation and becomes a latency requirement.

## Found while rechecking Parts 1–17

Nothing. `ruff` clean, the 40-question numbers unchanged, 611 tests green.

## What is next

**Part 19: upsert and delete by chunk id.** An append only adds; a changed chunk
must be replaced rather than duplicated, and a deleted one must stop appearing.
That is the part where the merge logic needs a tombstone.

## Checklist for this part

- [x] append writes a segment and leaves the base alone (asserted by mtime)
- [x] appended vectors are findable
- [x] merged search matches the exact scan (recall@5 = 1.0)
- [x] compaction absorbs segments and the vectors survive it
- [x] a corrupt segment is skipped, not fatal
- [x] benchmarked: 24× faster than a rebuild
- [ ] CLI wiring
- [ ] upsert / delete
