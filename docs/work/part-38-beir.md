# Part 38 — external datasets, and Phase 7 closed

**Wave 62 · `vrusha/eval_sets/beir.py`, `bench/registry.py`**

## What was built

A loader for the BEIR format — `corpus.jsonl`, `queries.jsonl`, `qrels/test.tsv` —
and a scorer that computes recall, precision, MRR and nDCG from **someone else's
relevance judgements**.

```
{'name': 'beir-mini', 'documents': 5, 'queries': 5, 'judgements': 5}
score(['d1','d3'], qrels['q1'], 5) → {'recall': 1.0, 'precision': 0.2,
                                      'mrr': 1.0, 'ndcg': 1.0}
```

A five-document fixture ships with the package, so the loader is exercised in CI
without a download.

## Datasets, not leaderboards

`MASTER-PLAN.md` refused to chase a published score, because the published suites
are saturated or contaminated. **That refusal does not extend to data.** A BEIR set
is queries and judgements; running it says whether this retriever works on someone
else's corpus. The score is ours to publish or not.

## The benchmark

```
read_ms=1.90  score_us=11.7  documents=5  queries=5
```

Reading the fixture costs **1.9 ms** and scoring one query **11.7 µs** — the loader
is not where time goes.

## What is NOT measured, and why it matters

**A real BEIR download.** `huggingface.co` answers `200`, but the dataset file
endpoints return `404` from this sandbox, and the official BEIR host
(`public.ukp.informatik.tu-darmstadt.de`) refuses the request.

So the loader is tested against a **bundled five-document fixture**, not against
scifact or fiqa. That is the honest state:

| what | status |
|---|---|
| the format | **tested** — 14 tests, including three malformed sets |
| the scorer | **tested** — against hand-computed recall/MRR/nDCG |
| a real BEIR set | **not run** — the download is blocked here |

The format is the part that has to be right, and a wrong loader would silently
score the wrong thing. The bytes are a download away, on a machine with access.

## Errors found and fixed

**None.** The refusals were designed in: a missing file names all three expected
files, bad JSON names the line, a short qrels row names the line, and a header row
is not counted as data.

## Phase 7, closed

| part | result |
|---|---|
| 36 JavaScript pages | seam; detection **26 µs**; browser unmeasured |
| 37 images | metadata **1.09 µs**; Tesseract unmeasured |
| 38 BEIR | loader **tested**; real dataset **not reachable here** |

**Three parts, three honest "not measured"** — all for the same reason: this sandbox
has no browser, no OCR binary and no dataset host. Each is a seam with a default
that declines, so the repository is not weaker for it.

## What is next

**Phase 8 — hardening (Parts 39–40):** the injection surface and fuzzing. The last
two parts, and the ones that test what happens when the input is hostile rather than
merely wrong.

## Checklist for this part

- [x] the BEIR format, read correctly (tested)
- [x] a missing file names all three expected files (tested)
- [x] bad JSON and a short qrels row name their line (tested)
- [x] a header row is not data; zero scores are not relevance (tested)
- [x] recall, precision, MRR and nDCG against hand-computed values (tested)
- [x] a bundled fixture so CI exercises the loader
- [x] benchmarked: 1.9 ms read, 11.7 µs score
- [x] **the real download is blocked, and the work log says so**
- [ ] hardening (Parts 39–40)
