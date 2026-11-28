# Part 29 — offload

**Wave 53 · `vrusha/store/compaction.py`, `bench/registry.py`**

## What was built

An entry past **20,000 tokens** is appended to the blackboard as an `artifact`
entry and replaced **in place** by a one-line reference:

```
[offloaded seq 7 -> board:417, 25,000 tokens]
```

The context keeps its shape — one line where the entry was, in the same order — and
loses only the bytes. Any agent can still read the full text from the board, and it
is still in the log.

**A reference beats a truncation.** `board:417` can be followed; a truncated
20,000-token page is just a shorter page with a hole in it, and nothing says where
the hole is.

## The benchmark

```
ms=0.122  offload_ms=3.719  offload_ratio=0.0
tokens_before=7579  tokens_after=1749  ratio=0.231  verified=1
```

An oversized entry becomes a reference in **3.72 ms**, and the ratio rounds to
**0.0** — a 25,000-token entry becomes about fifteen tokens.

## Errors found and fixed

**One, in my own test:** it called `tokens(...)` without the module prefix. Trivial,
and recorded because this is the fifth wave in a row where the failing line was the
test rather than the code.

## What it will not do

| limitation | why |
|---|---|
| compress the offloaded text | it is stored verbatim; a summary of a big page is Part 27's job, applied to it separately |
| follow the reference automatically | the caller reads `board:417`; nothing prefetches it (that is Part 30) |
| offload a *set* of entries that are collectively too big | the limit is per entry, not per window |

## Found while rechecking Parts 1–28

Nothing. `ruff` clean, 706 tests green.

## What is next

**Part 30: anticipation.** Prefetch the next task's context so the first turn does
not start cold — and bound it, because a prefetch that guesses wrong is wasted work
on a 2-vCPU machine.

## Checklist for this part

- [x] an oversized entry becomes a reference (tested)
- [x] the offloaded text is still readable from the board (tested)
- [x] a small entry is left alone (tested)
- [x] the token saving is reported (tested)
- [x] order is preserved (tested)
- [x] benchmarked: 3.72 ms, ratio 0.0
- [ ] automatic prefetch (Part 30)
