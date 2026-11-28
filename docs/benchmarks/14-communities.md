# Communities

**Benchmark:** `communities` · **measures:** label propagation over a known graph · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `nodes` | 200 |
| `communities` | 20 |
| `edges` | 180 |
| `ms` | 1.19 |

## What each number is

- `nodes` / `edges`: a synthetic graph of 20 clusters × 10 members, no cross-links
- `communities`: found — **20 of 20**, so the detector recovered the planted shape
- `ms`: the whole propagation, p50

## Why a synthetic graph

"Some number of communities" on a real corpus proves nothing — there is no gold
partition to compare against. A graph whose shape is *known* is the only way to say
whether the algorithm works: it either finds the 20 clusters or it does not.

## Method

Label propagation, sorted node order, ties to the smaller label, so the result is
reproducible. Harness: `bench/registry.py::bench_communities`.

## What this does not measure

Partition *quality* on real text. The detector is verified on a known shape and
untested against a human-labelled entity graph, which does not exist yet.
