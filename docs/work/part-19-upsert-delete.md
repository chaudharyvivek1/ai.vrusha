# Part 19 — upsert and delete: the newest mention wins

**Wave 44 · `vrusha/store/delta.py`, `vrusha/store/db.py`, `bench/registry.py`**

## What was built

- **`upsert()`** — writes the new vector *and* a tombstone for the old copy
- **`delete()`** — writes a tombstone and no vectors at all
- **the merge rule** — walking segments newest first, a chunk id is *settled* the
  moment a segment either provides it (live) or supersedes it (tombstone);
  everything settled is then dropped from the base results

## The rule, and why it has to be this one

An upsert is an append **plus a tombstone**. Without the tombstone the base keeps
serving the old vector while the segment serves the new one — the same chunk,
two answers, one of them stale. The failure is invisible in a test that only
checks "is the chunk findable"; it shows up as a result list where a document
appears at a score it no longer deserves.

```
newest → oldest
  segment C: provides 7  → 7 settled (live, from C)
  segment B: supersedes 7 → 7 settled (ignored)
  base:      has 7        → 7 settled (dropped)
```

## The benchmark

```
rebuild_ms=442.92  append_ms=14.06  upsert_ms=13.71  delete_ms=16.34
ratio=31.51        search_ms=18.94  new_vectors=200
```

An **upsert costs the same as an append** (13.7 ms vs 14.1 ms) and a delete costs
16.3 ms — against 443 ms for a rebuild, **31× more**. Search with four segments
attached costs 18.9 ms.

## Errors found and fixed

**1. `load_segments()` filtered on `rows` — so a delete-only segment was
discarded.** A tombstone segment has zero vectors by definition, so the filter
threw away exactly the segments that carry the deletes. Deleted chunks came back.
The filter now keeps a segment when it has rows **or** tombstones, with a comment
explaining why the obvious version is wrong.

**2. A deactivated chunk survived every index rebuild.** `iter_vectors()` never
joined `chunks`, so `AnnIndex.build()` indexed every vector in the table —
including chunks the merge pass had retired (`active = 0`). Compaction therefore
resurrected deleted chunks. This bug predates delta and would have been invisible
forever without a delete path to expose it. Fixed at the source, in
`iter_vectors()`, with the reason in the comment.

## What was pending

- CLI wiring (`vrusha index append|upsert|delete`)
- Versioned snapshots so a reader can keep a consistent view during a compaction
  (Part 21)

## Found while rechecking Parts 1–18

The two bugs above — both found *by* this part, in code from earlier waves. That
is the pattern this project keeps hitting: the new feature is the test the old
code never had.

## What is next

**Part 20: background compaction.** `should_compact()` exists and is capped, but
nothing calls it. The next part is the policy — when to compact, on which thread,
and how a query keeps working while it happens.

## Checklist for this part

- [x] upsert replaces rather than duplicates
- [x] delete removes a chunk from results
- [x] a chunk can be deleted and added again; the newest mention wins
- [x] a deleted chunk stays gone after compaction
- [x] the merged search still agrees with the store after edits
- [x] benchmarked: upsert ≈ append, both 31× faster than a rebuild
- [ ] CLI wiring
- [ ] snapshots during compaction
