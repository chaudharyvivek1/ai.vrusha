# Option 2 — the graph boost, on a corpus that has a graph

**Wave 67 · `vrusha/store/graphboost.py`, `tests/test_graphboost.py`**

## The result

| metric | boost off | boost on | delta |
|---|---|---|---|
| recall@1 | 0.400 | 0.400 | **+0.000** |
| recall@5 | 0.800 | 0.800 | **+0.000** |
| nDCG@10 | 0.656 | 0.656 | **+0.000** |
| MRR | 0.558 | 0.558 | **+0.000** |

Ten questions about the 15 company articles, 118 edges, 41 communities. **The boost
still changes nothing** — but this time I know why, and the first reason was a bug.

## The bug: the boost could never fire

```
_query_communities(store, "which company acquired Alpha Design Technologies")
  before:  set()      ← exact-match lookup, the query's entity is shorter
  after:   {0}
```

`_query_communities` looked up the query's extracted entity in `x_communities.name`
by **exact equality**. A question says `Alpha Design Technologies`; the graph stored
`Alpha Design Technologies Pvt Ltd`. **The lookup returned an empty set for all ten
questions**, so `boost()` had no communities to work with and every rank was
untouched.

**Part 26 measured this as a no-op on `docs/` and concluded "no edges". The real
reason was a dead code path that no corpus could have exercised.**

The fix is a **prefix** match — narrow on purpose: a prefix cannot merge two
different companies the way a similarity score can.

## Why it still does not move the numbers

With the match working, the boost fires and reorders nothing. The mechanism applies
`1 + weight` to at most `max_hits` candidates that share a community with the query;
at **118 edges over 152 nodes**, a candidate that shares a community is almost never
in the top 5 — the graph is too sparse for the signal to reach the ranking.

That is a property of the corpus, not of the code, and it is now measurable rather
than assumed.

## What this means for the default

`graph.boost` stays **off**, and now for a measured reason:

| | |
|---|---|
| the mechanism | works — fires, finds communities, never invents hits |
| the effect on this corpus | **0.000 on every metric** |
| the effect on `docs/` | 0.000, and it could not even fire |
| when it would pay | a graph dense enough that neighbours of a hit are plausible candidates |

## Errors found and fixed

**1. exact-match community lookup** — described above; the boost was unreachable.
**2. my measurement script stored edges twice** (`store_edges` then an `UPDATE`),
which is why the first run reported 118 edges from two passes. Harmless here, and
recorded because a benchmark that stores twice is a benchmark measuring the wrong
thing.

## What is next

The honest sequence is unchanged, and shorter now:

1. **the extraction fix** — a run truncated at the five-word cap creates the bare
   `Technologies` that no resolver should merge;
2. **a denser graph** — more articles, so a community has more than 8 members;
3. **a multi-step goal** — the one thing Part 35 could not test.

## Checklist

- [x] the boost measured on a corpus with 118 edges (it still does not help)
- [x] **the exact-match bug found and fixed** (tested: prefix matches, unrelated does not)
- [x] the boost still cannot invent hits (tested)
- [x] the reason for the zero written down, and it is not "no edges"
- [x] the default stays `off`, now for a measured reason
