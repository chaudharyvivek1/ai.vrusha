# A multi-step goal

**Benchmark:** `multistep` · **measures:** research three companies, then compare · **run:** 2026-11-28

## The numbers

| | single agent | orchestrated |
|---|---|---|
| facts found | 2 of 3 | **3 of 3** |
| context for the compare step | 22,970 chars | **921 chars** |
| seconds | 0.03–0.04 | 0.24–0.36 |
| board entries | — | 6 |

## What each number is

- **facts**: how many of the three companies appear in what the final step could see
- **context**: the characters the compare step was given — raw text for one agent,
  distilled findings for the hierarchy (**25× smaller**)
- **seconds**: the hierarchy is **~8× slower**, and both numbers are the finding

## Why this is the first win

Part 35 measured the hierarchy as identical quality with 42 board entries — **and its
workers were all failing silently** on a thread-bound SQLite connection. With that
fixed, the hierarchy does what it is for: step 2 receives *evidence* rather than raw
text, and covers every company instead of two.

## Method

38 company articles, three research tasks and one compare task in two stages.
Reproduced twice with identical results.
