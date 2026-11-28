# Part 26 — bounded graph ranking, and Phase 4 closed

**Wave 51 · `vrusha/store/graphboost.py`, `bench/registry.py`**

## What was built

A chunk whose entities sit in the same community as the query's entities gets a
**multiplicative, capped** boost: at most 15%, applied to at most 5 hits. Off by
default.

**The cap is arithmetic, not caution.** A graph built from prose extraction is
incomplete — `part-22-prose-entities.md` lists what the rules miss — so a graph
signal is evidence of a *possible* connection, not proof of a relevant one. An
unbounded boost would let a missing entity break a chain and a spurious one create
a false order.

## The measurement, and why it is a no-op

| metric | off | on | delta |
|---|---|---|---|
| recall@1 | 0.125 | 0.125 | **0** |
| recall@5 | 0.475 | 0.475 | **0** |
| nDCG@10 | 0.349 | 0.349 | **0** |
| passage@5 | 0.250 | 0.250 | **0** |

**The boost changes nothing on this corpus, and the reason is Part 23's finding:
`docs/` produces zero edges.** There is no graph to boost with. That is a coherent
result, not a failed feature — and it is exactly why the boost ships **off**.

## The caveat that the benchmark demonstrated on itself

```
ms=0.162  hits=20  reordered=0  edges=200
```

The first fixture was a *chained* graph — entity 0 linked to 1, 1 to 2, and so on.
Label propagation merges a chain into **one community**, so every hit was in the
query's community and every hit got the same boost. **A boost applied to everything
is a constant, and a constant reorders nothing** — the sentence was already in the
module docstring, and the benchmark then produced it.

The unit tests use a graph with two clusters, where the reorder is visible and
asserted.

## The verdict

**Built, bounded, off by default, and unproven on this corpus.** Turning it on
would be a preference, not an improvement. The same bar the lexical reranker
cleared and the cross-encoder failed.

## Phase 4, closed

| part | result |
|---|---|
| 22 entities from prose | 63,855 chars/s, 1,394 entities |
| 23 relations with evidence | 0 edges on docs — the corpus has no relations |
| 24 communities | 20 of 20 planted clusters, 1.19 ms |
| 25 multi-hop paths | 2-hop in 2.77 ms, hop cap enforced |
| 26 bounded boost | **off** — nothing to boost, and that is measured |

The honest summary of Phase 4: **the graph machinery works and this corpus cannot
exercise it.** A corpus of business or news prose would, and the parts are ready
for one.

## Found while rechecking Parts 1–25

Nothing. `ruff` clean, 685 tests green, coverage accepted the new module.

## What is next

**Phase 5 — compaction and anticipation (Parts 27–30).** The agent fabric's
load-bearing feature: an 85% budget, verifiable summaries carrying the `seq` range
they replaced, offload at 20,000 tokens, and prefetch.

## Checklist for this part

- [x] boost is off by default (asserted against the config)
- [x] a hit in the query's community moves up (tested)
- [x] the cap holds — a 50% deficit is not overturned by a 15% boost (tested)
- [x] no shared community means no change (tested)
- [x] an empty graph and no hits are not errors (tested)
- [x] only `max_hits` are boosted (tested)
- [x] measured on the 40 questions: no change, for a stated reason
- [x] benchmarked: 0.16 ms, and the `reordered=0` explained
