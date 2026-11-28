# The graph on a real corpus

**Benchmark:** `graph_real` · **measures:** the graph parts on a corpus with relations · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `articles` | 15 |
| `chars` | 153,787 |
| `entities` | 2,658 |
| **`edges`** | **124** |
| `nodes` | 159 |
| `communities` | 45 |
| `predicates` | 7 |
| `duplicates_merged` | 42 |

## Why this benchmark exists

`docs/` is technical prose, so `edges` reported **0** and Parts 23, 26 and 35 all
concluded "this corpus cannot exercise it". Same code, different corpus:

| | `docs/` | Wikipedia companies |
|---|---|---|
| edges | **0** | **124** |
| predicates | 0 | **7** |
| communities | — | **45** |

## Resolution, wired in

`entities.resolve` now runs over the graph's entity names. On this corpus it merges
**42 names**, taking 159 nodes to **157** and 45 communities to **42** — small, and
correctly small:

```
_ratio("alpha design technologies pvt ltd", "technologies")  -> 45.3
threshold                                                     -> 90.0
```

A bare suffix is **not** merged into a company, because it could belong to a dozen
of them. The remaining duplicates are an *extraction* artefact — a run truncated at
the five-word cap — not a resolution failure.

## What it does not measure

- **precision.** There is no gold graph, so 124 edges is *what the patterns found*.
- entity quality: one entity still appears at two lengths (`Technologies` and
  `Alpha Design Technologies Pvt Ltd`), which `entities.resolve` exists to fix and
  is not yet wired in.
