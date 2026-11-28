# Part 16 — the optional cross-encoder, and the honest verdict

**Wave 42 · `vrusha/rerank.py`**

## What was built

A cross-encoder reranker over **ONNX, not torch**: `onnxruntime` is ~15 MB and
pulls no CUDA; torch is ~800 MB and does. The rule from `docs/SURVEY.md` — the
core stays numpy-only — is kept: the backend is an optional extra, imported only
when chosen, and a machine without it falls back to the no-op.

- `_OnnxScorer` — one forward pass per batch of 8 pairs, sigmoid on the logit
- `CrossEncoderReranker` — `alpha` blend with the fused score, `scorer` injectable
  so the logic is testable without a 23 MB download
- registered as `cross-encoder`; `rerank.backend: cross-encoder` opts in

## What was measured, and what could not be

**Could not be measured on this machine.** The model loads — a 23 MB quantised
`ms-marco-MiniLM-L-6-v2` — and then:

1. **First attempt: out of memory.** ONNX tried to allocate **755 MB** for a
   single batch and failed on a 2 GB pod. The reranker had been handed *all 60
   candidates*, not the top of the list.
2. **After fixing that: it times out.** Batched at 8, windowed to 20, twelve
   questions did not finish inside 290 s on two shared vCPUs.

Both failures exercised the safety rule from Part 14 — the query still answered,
with the fused order and a note — which is the one genuinely useful result here.

**The window fix is a real bug, found by measuring.** A second stage is a
*re-ranker*, not a second search: it now receives `rerank.window` (default 20) of
the first stage's list. Handing it everything was both slower and fatal.

## The verdict (Part 17)

**Not adopted. The default stays `none`; the lexical reranker stays the opt-in.**

| reason | detail |
|---|---|
| cannot run here | 755 MB per batch, and slower than the timeout on 2 vCPUs |
| the bar was explicit | beat nDCG 0.611 **without** losing passage ranking |
| no number, no adoption | a reranker whose gain is unmeasured is a dependency with a story |

The seam means this costs nothing: the backend exists, is tested against a fake
scorer, and one config line turns it on where the memory exists.

## A finding that argues for the 40-question set

On a 12-question subset, the lexical reranker improved **everything** — nDCG@10
0.477 → 0.598, passage@5 0.333 → 0.500. On the full 40 it costs passage ranking.

| subset | nDCG@10 (none → lexical) | passage@5 (none → lexical) |
|---|---|---|
| first 12 | 0.477 → **0.598** | 0.333 → **0.500** |
| all 40 | 0.576 → **0.611** | 0.450 → 0.425 |

The same code, opposite conclusions, from the size of the set. **A small
evaluation set does not just give a noisy number — it gives a confidently wrong
one**, which is the strongest argument yet for Part 2 having grown the set to 40.

## Errors found and fixed

**1. The reranker was handed every candidate** (60), not the top of the list —
755 MB allocation, OOM. Fixed with a configurable window, and a test that asserts
the window is respected.

**2. Scoring was one batch of everything.** Now batched at 8.

## Found while rechecking Parts 1–15

Nothing. `ruff` clean, 40-question numbers unchanged, the lexical reranker still
opt-in at 0.3.

## What is next

Phase 3 — **incremental indexing** (Parts 18–21), which the outside review rated
a high-priority gap: today a new vector forces a rebuild.

## Checklist for this part

- [x] ONNX, not torch; optional; imported only when chosen
- [x] `scorer` injectable, so the logic is testable without the model
- [x] batched scoring
- [x] the reranker sees a window, not every candidate
- [x] falls back to the no-op when unavailable or when it fails
- [x] **verdict recorded: not adopted, with the reason being the machine**
