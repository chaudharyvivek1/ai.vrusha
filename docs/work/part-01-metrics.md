# Part 1 — the metrics

**Wave 37 · PR #33 · `vrusha/evaluate.py`**

## What was built

Six retrieval metrics and two answer metrics, plus a renderer that prints each
number beside its definition:

`recall@k` · `precision@k` · `MRR` · `nDCG@k` · `passage@k` · `citation accuracy`
· `faithfulness` (a labelled lexical proxy) · `vrusha evaluate [--answers] [--json]`

Every metric is checked against arithmetic done by hand in
`tests/test_evaluate.py` — a hit at rank 3 is `1/log2(4) = 0.5` nDCG, MRR `1/3`,
precision@5 `0.2`. If those drift, every benchmark in the repository drifts.

## What was pending

- The 40-question set with passage labels (Part 2)
- Cost per answered question (Part 4)
- External datasets — BEIR, CRAG subsets (Part 5)
- The reranker the numbers would justify (Phase 2)

## Errors found and fixed

**1. The table printed `citation_accuracy 0%` for a run that cited nothing.**
Found by *running* `vrusha evaluate`, not by reading it. The code returns `None`
for "nothing cited" and the renderer formatted it as a percentage anyway, which
reads as "every citation was wrong" — a much worse finding than "there were no
citations". Fixed in the renderer: it prints `n/a` with "(no answer cited)" when
`cited_questions` is zero.

**2. `--verbose` collided with the shared parent parser.** Every subcommand
already inherits `--verbose`; adding it again raised `argparse.ArgumentError` at
parser build time, so the whole CLI failed to start. Removed the duplicate.

**3. (Recheck, wave 38) An empty store scored 0% on every metric.** Running
`vrusha evaluate` against a store with no chunks produced a table of zeros, which
looks like a catastrophic retrieval failure rather than an empty database. It now
raises with the fix in the message: *"the store has no chunks, so there is nothing
to measure: `vrusha ingest --dir <path> --licence MIT` first"*.

## The first measurement

On this repository's own documentation, 12 questions:

| metric | value |
|---|---|
| recall@1 | 25% |
| recall@5 | 75% |
| MRR | 0.390 |
| nDCG@10 | 51% |
| passage@5 | 42% |

Three questions miss the top five entirely — stale results after new pages,
address collisions, splitting a document. **All three are ranking failures, not
parsing failures**, which is what decided the order: the reranker (Phase 2) comes
before the document work (Phase 1).

## What is next

Part 2: grow the set from 12 to 40 questions with passage labels, and make the
loader refuse a malformed label — because a benchmark nobody validates is a
benchmark that quietly stops measuring.

## Checklist for this part

- [x] every metric defined beside its number
- [x] every metric checked against hand arithmetic
- [x] `None` for "nothing cited", never `0`
- [x] faithfulness labelled a proxy
- [x] CLI, `--json`, `--verbose`
- [x] empty store refused with the fix in the message
- [ ] cost per question (Part 4)
- [ ] external datasets (Part 5)
