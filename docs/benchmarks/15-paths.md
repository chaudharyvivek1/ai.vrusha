# Multi-hop paths

**Benchmark:** `paths` · **measures:** two-hop search over a chained graph · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `edges` | 199 |
| `hops` | 2 |
| `ms` | 2.77 |

## What each number is

- `edges`: a 20-cluster chained graph — 20 clusters of 10, each linked to the next
- `hops`: the length of the path found between two entities two clusters apart
- `ms`: breadth-first search, p50

## Why this graph

A path search is only interesting if a path *exists and is not trivial*. The chain
means a 2-hop path crosses a cluster boundary, so the search cannot succeed by
finding a sibling.

## Method

Breadth-first, `max_hops` enforced, every hop carrying its sentence. Harness:
`bench/registry.py::bench_paths`.

## What this does not measure

Path *quality* on real prose — the same limit as the entity and relation reports,
because there is no labelled graph yet.
