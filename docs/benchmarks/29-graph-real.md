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

## Why this benchmark exists

`docs/` is technical prose, so `edges` reported **0** and Parts 23, 26 and 35 all
concluded "this corpus cannot exercise it". Same code, different corpus:

| | `docs/` | Wikipedia companies |
|---|---|---|
| edges | **0** | **124** |
| predicates | 0 | **7** |
| communities | — | **45** |

## What it does not measure

- **precision.** There is no gold graph, so 124 edges is *what the patterns found*.
- entity quality: one entity still appears at two lengths (`Technologies` and
  `Alpha Design Technologies Pvt Ltd`), which `entities.resolve` exists to fix and
  is not yet wired in.
