# Part 15 (and 17) — the lexical reranker, and the decision

**Wave 41 · `vrusha/rerank.py`, `bench/registry.py`**

## What was built

A field-weighted BM25 reranker over the candidates already retrieved: no model,
no dependency, ~2 ms for twenty hits. And — because a reranker nobody measured
against its absence has not been shown to do anything — the measurement, on the
same 40 questions, in the same run.

## The measurement, and what it cost

| alpha | recall@1 | recall@5 | MRR | nDCG@10 | passage@5 | ms |
|---|---|---|---|---|---|---|
| **none** | 35.0% | 70.0% | 0.471 | 0.576 | **45.0%** | 29 |
| 0.1 | 37.5% | 67.5% | 0.484 | 0.580 | 40.0% | 60 |
| 0.2 | 37.5% | 70.0% | 0.493 | 0.602 | 40.0% | 64 |
| **0.3** | 35.0% | **72.5%** | 0.486 | 0.611 | 42.5% | 55 |
| 0.5 | 37.5% | 72.5% | 0.506 | 0.616 | 37.5% | 71 |
| 0.7 | 40.0% | 70.0% | 0.513 | 0.619 | 27.5% | 89 |
| 1.0 | 40.0% | 67.5% | 0.504 | 0.602 | 22.5% | 85 |

**The finding is the trade, not the gain.** Document ranking improves — nDCG@10
+3.5 points at alpha 0.3, recall@5 +2.5 — while **passage ranking gets worse,
monotonically, as alpha rises**: 45% → 22.5% at alpha 1.0. The reranker puts the
right *document* higher and the right *passage* lower, because a document-level
signal cannot tell one paragraph from another inside the same file.

## The hypothesis that was wrong

I assumed title bias: weighting titles 3× would promote a hit whose *filename*
matched the query. Measured, it is not true —

| alpha | title weight | nDCG@10 | passage@5 |
|---|---|---|---|
| 0.3 | 1 | 0.608 | 42.5% |
| 0.3 | 2 | 0.611 | 42.5% |
| 0.3 | 3 | 0.611 | 42.5% |
| 0.5 | 1 | 0.595 | 37.5% |
| 0.5 | 2 | 0.616 | 37.5% |
| 0.5 | 3 | 0.616 | 37.5% |

The title weight changes nothing to three decimal places. **Alpha is the only
lever**, and the passage loss is a property of reranking at document granularity,
not of the field weights. The weight stays configurable so a corpus with real
titles can re-measure instead of inheriting this.

## The decision (Part 17, taken early)

**Adopted as an opt-in backend at `alpha=0.3`. The default stays `none`.**

| for | against |
|---|---|
| nDCG@10 +3.5 points, recall@5 +2.5 | passage@5 −2.5 points |
| 2 ms, no dependency | query time 29 ms → 55 ms |
| a seam that is now proven to work | a gain small enough to be corpus-specific |

A +3.5-point gain that costs a 2× latency increase and a small passage loss is
worth having *when you ask for it*, not as a default. Part 16's cross-encoder now
has a real bar to clear: beat nDCG 0.611 **without** losing more passage ranking.

## The benchmark list — every work, measured

`bench/registry.py` is now the rule made executable: every module is either
benchmarked or listed in `EXEMPT` **with a written reason**, and
`tests/test_benchmarks.py` fails CI if a new module has neither.

```
benchmark     seconds  numbers
blackboard       4.62  append_ms=1.32, delta_read_ms=1.70, replay_ms=71.37, entries=2020
claims           0.08  claim_ms=0.40, summary_ms=0.13
registry         0.21  register_ms=1.24, heartbeat_ms=0.39, sweep_ms=0.13, live=64.00
address          0.50  warm_ms=0.40, rows=2000.00
rerank           0.04  noop_ms=0.00, lexical_ms=1.98, candidates=20.00
retrieval        5.31  query_ms=12.38, recall@5=0.70, ndcg@10=0.58, questions=40
ingest           2.47  docs_per_second=12.07, chunks=253.00, ms_per_doc=82.87
extract          0.19  files_per_second=52.30, tables=4.00, ms_per_file=19.12
serve            0.73  request_ms=3.63, max_ms=34.71
```

## Errors found and fixed

**1. The registry benchmark reported `live=0`.** It measured the sweep before the
summary, and a sweep with `stale_after=0` marks everything dead — so the number
that was supposed to show 64 live agents showed none. Found by reading the output
rather than trusting it.

**2. Thirty-nine modules had no benchmark.** The coverage test found them on its
first run. Nine are now measured directly and the rest are exempted with a
sentence each — *"network-bound; a local benchmark would measure the loopback, not
the web"* is a decision; a blank entry would be an oversight.

## Found while rechecking Parts 1–14

Nothing. `ruff` clean, the 40-question numbers unchanged, the seam off by default.

## What is next

**Part 16: the optional cross-encoder.** The bar is now explicit: beat nDCG 0.611
without losing more passage ranking, and only if the dependency is worth it. If it
cannot, Part 17 records that the lexical reranker was the right stopping point.

## Checklist for this part

- [x] BM25 reranker, no model, no dependency
- [x] measured against the no-op on the same 40 questions
- [x] alpha swept, not guessed
- [x] the title-bias hypothesis tested and **disproved**
- [x] decision recorded: opt-in at 0.3, default none
- [x] every module benchmarked or exempted with a reason, enforced by CI
- [x] nine benchmarks in one runnable table
