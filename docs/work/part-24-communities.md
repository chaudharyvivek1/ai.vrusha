# Part 24 — communities by label propagation

**Wave 49 · `vrusha/store/communities.py`, `bench/registry.py`**

## What was built

Label propagation over the edge graph: every node starts in its own community, and
in each round it adopts the most common label among its neighbours. Twenty rounds
at most, then the ids are renumbered densely in sorted order.

**Why not Louvain:** it finds slightly better partitions and brings modularity
arithmetic plus a tie-breaking rule that has to be argued about. For a graph of
this size the difference is not worth the surface area.

**The rule that makes it testable:** nodes are visited in **sorted order**, and a
tie between two candidate labels goes to the **smaller** one. A detector that gives
a different partition on a second run cannot be cited, so determinism is asserted
directly — five repeat runs, same answer.

## The benchmark

```
nodes=200  communities=20  edges=180  ms=1.19
```

A synthetic graph of 20 clusters × 10 members with no cross-links: **20 communities
found, in 1.19 ms.** A known-shape graph is the right benchmark here — "some number
of communities" on a real corpus proves nothing without a gold partition.

## Errors found and fixed

**None in this part.** The determinism rule was designed in rather than discovered:
the first sketch used `max(counts, key=counts.get)`, which returns the first key in
dictionary-insertion order and therefore varies with how the edges arrived. It was
replaced with an explicit `min(label for label, count in … if count == best)`
before any test ran.

## What it will not do

| limitation | why |
|---|---|
| overlapping communities | one label per node; a company in two groups gets one |
| weighted edges | a 0.9-confidence edge counts the same as a 0.7 |
| a graph larger than a few thousand nodes | O(rounds × nodes × degree), fine here, not at scale |

## Found while rechecking Parts 1–23

Nothing. `ruff` clean, 662 tests green, coverage accepted the new module.

## What is next

**Part 25: multi-hop expansion.** Communities give a cluster; multi-hop gives a
*path* — "which two entities connect this one to that one" — which is the query a
flat retrieval cannot answer at all.

## Checklist for this part

- [x] two disjoint triangles are two communities (tested)
- [x] a chain is one community (tested)
- [x] the same answer on five repeat runs (tested)
- [x] an isolated node is a singleton (tested)
- [x] self-loops and empty graphs are not errors (tested)
- [x] ids are dense and stable (tested)
- [x] stored, with `community_of()` and `members()` ordered by degree
- [x] benchmarked on a graph whose shape is known
- [ ] multi-hop paths (Part 25)
