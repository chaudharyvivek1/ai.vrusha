# Option 1 — a corpus that is not documentation

**Wave 65 · `examples/corpora/wiki-companies/`, `vrusha/store/prose.py`, `bench/registry.py`**

## Why this was the next work

Three parts ended with the same sentence: **"this corpus cannot exercise it."**

| part | its conclusion |
|---|---|
| 23 relations | `edges=0` — `docs/` has no business relations |
| 26 graph boost | nothing to boost, so the default stayed `off` |
| 35 orchestration | identical quality; no multi-step goal to test |

Every one of those was a statement about *documentation*, not about the code.

## What was fetched

**15 Wikipedia company articles**, 154 KB of real prose — TCS, Infosys, Wipro, Reliance,
HDFC, ICICI, Adani, JSW Steel, Titan, ITC, NTPC, Bharat Petroleum, GAIL and others.

Wikipedia's API answers from this sandbox (`200`); the bulk endpoint rate-limits at
**429** after about ten titles, so they are fetched one at a time with a pause, and
five were skipped. **That is a limit of this machine, not of the corpus** — the
fixture is 15 articles and the fetch is reproducible.

## The measurement

```
articles=15  chars=153,787  entities=2,658  edges=124
nodes=159  communities=45  predicates=7
```

| | `docs/` | Wikipedia companies |
|---|---|---|
| entities | 1,394 | **2,658** |
| organisations | — | **458** |
| **edges** | **0** | **124** |
| predicates | 0 | **7** |
| communities | — | **45** from 159 nodes |

**The graph works. It was the corpus that had no relations.**

Seven predicates fire: `acquired`, `appointed`, `located_in`, `partnered_with`,
`reported`, `supplies`, `works_for`. The first edge found is real:

```
acquired   Adani Defence Systems -> Alpha Design Technologies Pvt Ltd
```

## The bug the real corpus found in ten minutes

```
In April Adani Defence Systems -> Alpha Design Technologies Pvt Ltd
```

A leading **preposition and month** were part of the entity name. The documentation
corpus never produced a run long enough to expose it, because technical prose does
not begin sentences with `In April <Company>`.

Fixed: leading articles, prepositions *and* month names are stripped from a run.
That is the fourth time in this programme that a new corpus found what the tests
could not — and the first time it was a corpus rather than a feature.

## What is still wrong, and now visible

| issue | example |
|---|---|
| a run can cross a clause boundary | `PLR Systems On September Adani` |
| one entity appears twice at different lengths | `Technologies` and `Alpha Design Technologies Pvt Ltd` |
| the person extractor finds almost nothing | 1 person in 154 KB |

The first is a sentence-splitting weakness (`On September` has no full stop before
it); the second is what `entities.py` resolution exists for and is not wired to the
graph yet; the third needs the honorific rule to fire more often, or a model.

## Found while rechecking Parts 1–40

The `prose.py` fix above — found by running the new corpus, not by reading the code.

## What is next

1. **wire `entities.resolve` into the graph** — the duplicate-length problem above is
   exactly what it was built for in Part 26 and never connected to;
2. **a multi-step goal on this corpus**, which would finally test Part 35's
   orchestration claim instead of only its cost;
3. **fetch more articles** on a machine without the 429 limit.

## Checklist

- [x] a real, non-documentation corpus, in the repo as a fixture
- [x] the graph parts run on it: 2,658 entities, **124 edges**, 45 communities
- [x] the fetch is reproducible and its rate limit recorded
- [x] a real bug found and fixed (leading preposition and month in a name)
- [x] benchmarked: `graph_real`
- [x] what is still wrong, written down
