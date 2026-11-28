# Anticipation

**Benchmark:** `anticipate` · **measures:** the prefetch cache — put, hit, miss · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `put_ms` | 0.0028 |
| `hit_ms` | 0.0008 |
| `miss_ms` | 0.0006 |
| `cached` | 1 |
| `budget_ms` | 500 |
| `max_tasks` | 3 |

## What each number is

- `hit_ms`: 0.8 µs, against a ~9 ms retrieval — **the saving is the whole query**
- `budget_ms` / `max_tasks`: the caps. Prefetching stops at 500 ms and never guesses
  more than three tasks ahead

## Why the caps are the design

Anticipation is a bet, and the caps decide what a wrong bet costs: bounded work
(≤3 tasks, ≤500 ms), never a run. The cache itself is 32 entries with a 60-second
TTL — a stale prefetch is worse than none, because it looks like a warm hit.

## Method

In-process, this pod. Harness: `bench/registry.py::bench_anticipate`.

## What this does not measure

How often the guess is *right*. That is the number that would justify the feature,
and it needs a real task stream to measure — recorded as the open question.
