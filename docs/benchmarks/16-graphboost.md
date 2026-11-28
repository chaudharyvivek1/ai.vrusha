# The bounded graph boost

**Benchmark:** `graphboost` · **measures:** the graph boost over 20 hits · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `edges` | 200 |
| `hits` | 20 |
| `ms` | 0.16 |
| `reordered` | 0 |

## What each number is

- `edges`: a chained graph of 200 edges
- `ms`: the boost applied to 20 hits, p50
- `reordered`: how many hits changed position

## Why `reordered` is 0, and why that is correct

A **chain** is one community: label propagation merges entity 0 → 1 → 2 → … into a
single label. So every hit shares the query's community, every hit gets the same
15% boost, and **a constant reorders nothing**.

That is the caveat written in the module docstring, demonstrated by the benchmark
itself. The unit tests use a two-cluster graph, where the reorder is visible.

## The measurement that matters

On the 40-question set the boost changes **no metric at all** — because `docs/`
produces zero edges (`13-edges.md`). There is no graph to boost with.

So it ships **off**. Turning it on would be a preference, not an improvement.

## Method

Multiplicative, capped at 15%, at most 5 hits. Harness:
`bench/registry.py::bench_graphboost`.
