# Part 4 — cost accounting

**Wave 39 · `vrusha/evaluate.py`, `vrusha evaluate`**

## What was built

Every question now records what it cost, in the three currencies that decide
whether a system is usable:

| measure | what it is | why it matters |
|---|---|---|
| `context_tokens` | the retrieved text, estimated at 4 chars/token | this is the input bill of any model you put in front of it |
| `answer_tokens` | the answer itself | the output bill |
| `seconds` | wall clock, per question and per **answered** question | latency is a cost, and a refused question is cheap and useless |
| `usd` | at a configured price per 1,000 tokens | zero by default, and it says so |

```
cost
------------------------------------------------------------------------------
answered                 40  questions with an answer, of 40
context tokens          986  retrieved text, estimated at 4 chars/token
answer tokens           232  the answer itself
seconds/answered       0.03  wall clock for a question that got an answer
cost/question         local  no model was called, so there is no token price
```

## Two decisions worth stating

**The price defaults to zero, and the table prints `local` rather than `0.0000`.**
The default answerer is extractive and local: it calls no model, so it costs no
tokens. Printing a price for a model nobody called would be the worst kind of
number — precise, plausible, and about something that did not happen.

**Cost is reported per question *and* per answered question.** They are different
and the gap is the point: a system that refuses half its questions is cheap per
question and expensive per answer. Averages over refused questions hide exactly
the failure that matters.

**Tokens are estimated, not counted.** A real tokeniser would be exact, and would
also make two runs incomparable when different tokenisers are installed. The
estimate is labelled everywhere it appears.

## What was pending

- Nothing on the plan's list for this part.
- The prices are not yet per-provider (one `price_per_1k_in` for the whole run).

## Errors found and fixed

**1. My own test arithmetic, twice.** `context_tokens` is a mean over *all*
questions, so 400 characters (100 tokens) beside a refused question that
retrieved nothing averages to 50, not 100. The code was right; the expectation
was wrong. Written down because it is the third time in this project that a test
expectation, not the implementation, was the bug — and the pattern is worth
noticing: I write the expectation from the intent, not from the arithmetic.

## Found while rechecking Parts 1–3

Nothing. `ruff` clean, the set loads, `evaluate` runs, and the wheel still
contains the eval set. Recorded because "we looked and it was fine" is a
different statement from "we did not look".

## What is next

**Phase 2, Part 14: the reranker seam.** Phase 0 is now complete — quality is
measured on 40 questions and cost is measured on the same run. The three questions
that miss the top five are the reranker's first target, and Part 17 will decide
whether it earned its place.

## Checklist for this part

- [x] tokens, seconds, money recorded per question
- [x] cost per question **and** per answered question
- [x] price defaults to zero and the table says `local`
- [x] the estimate is labelled an estimate
- [x] tests against hand arithmetic
- [ ] per-provider prices
