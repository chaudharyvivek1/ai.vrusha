# Part 14 — the reranker seam

**Wave 40 · `vrusha/rerank.py`, `vrusha/store/query.py`, `vrusha evaluate --no-rerank`**

## What was built

A seam, not a reranker:

- a **protocol** — `name`, `available()`, `rerank(query, hits, k)`
- a **no-op** as the default, which changes nothing and says so
- a **registry** (`RERANKERS`) so a backend is named in config, never hard-coded,
  and a module that needs a model is imported only when it is chosen
- **`apply()`**, which enforces three rules: a reranker may reorder and drop, it
  may not **invent** a hit it was not given, and if it raises the query still
  answers with the fused order plus a note
- `retrieve(..., reranker=, rerank_off=)` and `vrusha evaluate --no-rerank`

## Why a seam before a reranker

Without one, "add reranking" means editing the retriever — and then there is no
way to run the same 40 questions with and without it on the same day. The seam is
what makes Part 17's comparison possible at all, and what lets the answer be
"none of them" without deleting code.

## What was pending

- The lexical reranker (Part 15)
- The optional cross-encoder (Part 16)
- The decision (Part 17)

## Errors found and fixed

**1. `rerank_off` only suppressed the *configured* reranker, not one passed in
explicitly.** Found by the test that asserts the opt-out means the opt-out. The
whole point of the switch is "run the same query without a second stage", so it
now beats both the config and an explicit argument.

**2. The seam had to be threaded through `evaluate.run()`** — otherwise there is
no way to compare. Added as keyword arguments, so every existing caller is
unaffected.

## Found while rechecking Parts 1–4

Nothing. `ruff` clean, the 40-question numbers unchanged (35% / 70% / 70%),
`evaluate` runs, the set loads.

## The guarantee that matters

A second stage is a new failure mode in the query path. Three rules make it safe:

| rule | why |
|---|---|
| it may not invent a hit | a reranker that returns a chunk it was not given is a correctness bug that would look like a better result |
| a raise degrades, never fails | the first stage already produced a defensible order |
| `rerank_off` beats everything | a comparison that can be overridden by config is not a comparison |

## What is next

**Part 15: the lexical reranker** — field-weighted BM25 over the top twenty, with
the title and symbol weighted. No model, no dependency, measurable against the
no-op on the same 40 questions. If it does not improve nDCG@10, the part is
reverted, and that is recorded.

## Checklist for this part

- [x] protocol, no-op, registry
- [x] built from config, imported lazily
- [x] may reorder and drop, may not invent
- [x] a failing reranker degrades with a note
- [x] `rerank_off` beats config and argument
- [x] `evaluate --no-rerank` for the like-for-like comparison
- [x] no-op produces identical results (the acceptance test)
