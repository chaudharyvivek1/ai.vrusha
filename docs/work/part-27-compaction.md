# Parts 27–28 — compaction, and a summary you can check

**Wave 52 · `vrusha/store/compaction.py`, `bench/registry.py`**

## What was built

- **compact at 85%** of a token budget (`THRESHOLD`), and **offload** a single entry
  past 20,000 tokens rather than summarising it
- a rule-based summary: every `decision`, `error`, `question` and artifact
  reference kept, the **last finding per task** kept, `progress` dropped, the last
  five entries kept verbatim
- **every summary carries the `seq` range it replaced**, and `verify()` checks the
  summary against the entries in that range

## The benchmark

```
entries=53  ms=0.13  tokens_before=7579  tokens_after=1749
ratio=0.231  verified=1.0
```

A realistic run of 53 entries: **7,579 tokens → 1,749 (23%)**, verified, in 0.13 ms.

## Why verification is the whole feature

`docs/AGENTS.md` says a summary that silently drops a fact produces confident wrong
answers — worse than no compaction, because it is compaction you trust. So:

| check | what it catches |
|---|---|
| every `decision`, `error`, `question` appears in the text | a dropped decision |
| every artifact reference is listed | a lost pointer to a file |
| `seq_from` matches the first entry replaced | a summary claiming the wrong range |

Two tests break a summary deliberately — replacing a decision with "nothing", and
deleting an artifact reference — and assert `verify()` fails. A summary that fails
verification is not used.

## Errors found and fixed

**1. The first summary was 2.7× LARGER than its input.** With six small entries, the
overhead of the header and the kept categories exceeded the text it replaced. Fixed
by making compaction **decline**: `useful=False`, `text=""`, and the caller keeps
the entries. A compaction that grows the text has made the caller slower and no
better off, and it must say so rather than return a bigger string.

**2. `locals()[kind]` looked up a singular name that only exists in the plural** —
a `KeyError` on the first real run, because `kept` was computed from local names
`decision`/`error` while the variables are `decisions`/`errors`. Replaced with an
explicit mapping.

**3. My own test fixtures were too small to shrink.** Three of them asserted that
kept categories survived while the fixture had almost nothing to drop — so
compaction correctly declined and the assertion failed. The fixtures now pad with
30 droppable entries, which is the case compaction exists for.

## What it will not do

| limitation | why |
|---|---|
| summarise *meaning* | it selects and copies; a model would paraphrase and could invent |
| keep an entry that looks important but is not a kept kind | only the five kinds are protected |
| compact a run with nothing droppable | it declines, correctly, and says so |

## Found while rechecking Parts 1–26

Nothing. `ruff` clean, 692 tests green.

## What is next

**Part 29: offload.** `offload_needed()` exists; the next part writes the oversized
entry to the blackboard and leaves a reference where it was, so the context keeps
its shape and loses only the bytes.

## Checklist for this part

- [x] 85% threshold, tested at both ends
- [x] the summary records the `seq` range it replaced (tested)
- [x] decisions, errors, questions, artifacts survive (tested)
- [x] only the last finding per task survives (tested)
- [x] progress is dropped and counted (tested)
- [x] **compaction declines when it would grow the text** (tested)
- [x] `verify()` catches a dropped decision and a dropped artifact (tested)
- [x] benchmarked: 23% of the original, 0.13 ms, verified
- [ ] offload (Part 29)
