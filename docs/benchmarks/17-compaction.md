# Compaction

**Benchmark:** `compaction` · **measures:** fold a run into a verified summary · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `entries` | 53 |
| `ms` | 0.13 |
| `tokens_before` | 7,579 |
| `tokens_after` | 1,749 |
| `ratio` | 0.231 |
| `verified` | 1 |
| `offload_ms` | 3.72 |
| `offload_ratio` | 0.0 |

## What each number is

- `entries`: 53 entries — 40 progress, 10 findings, 1 decision, 1 error, 1 artifact
- `ratio`: output tokens over input tokens — **0.231 means 23% of the original**
- `verified`: 1 when `verify()` found no dropped decision, error or artifact

## The number that matters most

`verified=1`. A summary is only usable if the things that must survive did survive,
and that is checked against the entries it claims to replace — not assumed.

## The offload path

An entry past 20,000 tokens is written to the blackboard and replaced by a
reference: `[offloaded seq 7 -> board:417, 25,000 tokens]`. That costs **3.72 ms**
and the ratio rounds to **0.0** — a 25,000-token entry becomes about fifteen tokens,
and the full text is still readable at `board:417`.

A reference beats a truncation: it can be followed, and it says where the bytes went.

## Method

Rule-based selection, no model. Harness: `bench/registry.py::bench_compaction`.

## What this does not measure

Whether the summary is *faithful* in the way a model-written summary would be
judged — it copies rather than paraphrases, so it cannot invent, and it also cannot
compress prose that has no droppable entries. When it cannot shrink, it declines
(`useful=False`) rather than returning something bigger.
