# Option 2b — a denser graph, and the boost still does nothing

**Wave 69 · `examples/corpora/wiki-companies/` (38 articles), `bench/registry.py`**

## What was fetched

**23 more articles** with **backoff instead of skipping** — 2s, 5s, 8s between
retries — taking the corpus from 15 to **38 articles, 363 KB**. Three titles were
given up on after four attempts.

## The graph, at twice the density

| | wave 65 | now |
|---|---|---|
| articles | 15 | **38** |
| edges | 124 | **296** |
| nodes | 159 | **390** |
| communities | 45 | **115** |
| largest community | 8 | **16** |
| chunks with a community | 15 | **37** |

## The boost, again

| metric | off | on | delta |
|---|---|---|---|
| recall@1 | 0.333 | 0.333 | **+0.000** |
| recall@5 | 0.667 | 0.667 | **+0.000** |
| nDCG@10 | 0.537 | 0.537 | **+0.000** |
| MRR | 0.461 | 0.461 | **+0.000** |

15 questions, 296 edges, 115 communities, the query matching community `{0}`, **37 of
38 chunks mapped**. Everything the boost needs is present, and it moves nothing.

## What this settles

Wave 67 found the boost could not *fire* (an exact-match bug). Wave 69 shows that
with the bug fixed **and** the graph 2.4× denser, it still does not *help*.

| | |
|---|---|
| the mechanism | correct — fires, finds communities, never invents hits |
| the data | present — 296 edges, 37 chunks mapped |
| the effect | **0.000 on every metric, twice** |
| the conclusion | **keep it off**; the graph's value is the entity and relation data, not a ranking boost |

That is now a measured conclusion rather than a corpus gap, and the default has not
changed: `graph.boost: false`.

## What else the bigger corpus shows

**Retrieval got harder, as it should:** recall@1 fell 0.400 → 0.333 and recall@5
0.800 → 0.667 with 38 documents instead of 15. A benchmark whose numbers do not move
when the corpus grows is measuring the corpus, not the retriever.

## Errors found and fixed

**None.** The backoff worked on the first attempt for 23 of 26 titles.

## Found while rechecking

Nothing. `ruff` clean, 880 tests green.

## What is next

**Option 3: a multi-step goal** — the one thing Part 35 could not test, and the only
remaining question with a real answer behind it.

## Checklist

- [x] 23 more articles, with backoff rather than skipping
- [x] the graph at 2.4× density: 296 edges, 115 communities, 37 chunks mapped
- [x] **the boost measured again: 0.000 on every metric**
- [x] the conclusion stated: keep it off, for a measured reason
- [x] retrieval difficulty noted (recall@1 0.400 → 0.333 with a bigger corpus)
