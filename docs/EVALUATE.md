# Evaluation: the numbers that make other claims checkable

Part 1 of `docs/PLAN-V3.md`. Without this, every other part is judged by
impression.

```bash
vrusha evaluate              # retrieval on the built-in 12 questions
vrusha evaluate --answers    # also compose answers, to measure citations
vrusha evaluate --json       # per-question detail
```

```
metric                value  definition
------------------------------------------------------------------------------
recall@1                25%  the right document is the top hit
recall@5                75%  the right document is in the top five
precision@5             17%  share of the top five that are right
mrr                   0.390  1/rank of the first right document
ndcg@10                 51%  position-weighted gain over the top ten
passage@5               42%  a top-five passage contains the needed words
citation_accuracy       n/a  the answer cited the document holding the answer (no answer cited)
```

**These are the first measured numbers for the whole pipeline**, on this
repository's own documentation, and they are worse than the retrieval-only figures
in `docs/BENCHMARKS.md` — which is the point of measuring: 75% recall@5 means one
question in four does not have its answer in the top five at all.

## What each metric is for

| metric | what it catches |
|---|---|
| recall@k | the right document is missing entirely |
| precision@k | the right document is buried under noise |
| MRR | the right document is present but ranked low |
| nDCG@k | position-weighted: a hit at rank 1 is worth more than at rank 5 |
| passage@k | the right document, but not the passage that answers it |
| citation accuracy | the answer cited something other than what holds it |
| faithfulness | the answer contains words the context never had |

## Two numbers that are deliberately not numbers

**Citation accuracy is `n/a`, not `0%`, when nothing was cited.** "Cited nothing"
and "cited the wrong thing" are different failures, and averaging them hides both.
The code returns `None`, and the table prints `n/a`.

**Faithfulness is a proxy and says so.** It counts the share of the answer's
content words that appear in the cited chunks. It catches an invented name, number
or term. It cannot catch an answer that is faithful in words and wrong in meaning.
Calling it "faithfulness" and stopping there would be the kind of number this
project has refused since wave 20.

## The set

Forty questions about this repository (`vrusha/eval_sets/docs-40.json`), phrased
the way a person would phrase them
— deliberately avoiding the words the target document uses, because a question
that repeats the title only tests the keyword index. Each carries the document that
answers it and the words a correct passage must contain.

Every question carries the document that answers it and the words a correct
passage must contain, and `tests/test_evaluate.py` checks that **every keyword
really appears in its own document** — a keyword that is not there makes
`passage@k` unachievable and turns the metric into a measure of the labels.

Part 5 adds
BEIR and CRAG **subsets** — datasets, not leaderboards, which is a change from
`MASTER-PLAN.md` and is explained in `PLAN-V3.md` Part B.

## Cost

Every question also records what it cost: context tokens, answer tokens, wall
clock, and money at a configured price per 1,000 tokens.

```
cost
------------------------------------------------------------------------------
answered                 40  questions with an answer, of 40
context tokens          986  retrieved text, estimated at 4 chars/token
answer tokens           232  the answer itself
seconds/answered       0.03  wall clock for a question that got an answer
cost/question         local  no model was called, so there is no token price
```

**The price defaults to zero and prints `local`**, because the default answerer is
extractive and calls no model — a price for a model nobody called would be
precise, plausible, and about something that did not happen.

**Cost is per question and per answered question.** They differ, and the gap is
the finding: a system that refuses half its questions is cheap per question and
expensive per answer.

Tokens are **estimated** at 4 characters each, and labelled an estimate — a real
tokeniser would make two runs incomparable when different tokenisers are installed.

## What the first run says

`recall@1` is 25% and `recall@5` is 75%. Three of twelve questions miss the top
five entirely: a question about stale results after new pages, one about address
collisions, and one about splitting a document. Those three are the first
candidates for the reranker in Phase 2 — and if the reranker cannot lift them,
that is the answer to "was it worth it".
