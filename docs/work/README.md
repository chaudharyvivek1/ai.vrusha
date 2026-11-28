# The work log

Every part of `docs/PLAN-V3.md` gets a file here when it is finished, and this
page is the checklist. A part is only "done" when its file exists and says what
broke.

**Why this exists:** `CHANGELOG.md` records what shipped. This records what
*happened* — what was pending, what was broken, how it was found, and what the
next goal is. Those are different questions, and the second one is the one asked
at the start of the next part.

## The checklist

Status: `[x]` done · `[~]` partly · `[ ]` not started · `[-]` deliberately skipped

### Phase 0 — measurement

| # | part | status | file |
|---|---|---|---|
| 1 | metrics: recall, precision, MRR, nDCG, citation, faithfulness | `[x]` | [part-01](part-01-metrics.md) |
| 2 | the 40-question evaluation set, with passage labels | `[x]` | [part-02](part-02-eval-set.md) |
| 3 | `vrusha evaluate --set` — one command, one table | `[x]` | [part-02](part-02-eval-set.md) |
| 4 | cost accounting per answered question | `[x]` | [part-04](part-04-cost.md) |
| 5 | BEIR / CRAG subset loader (optional, cached, never in CI) | `[ ]` | — |

### Phase 1 — documents

| # | part | status |
|---|---|---|
| 6 | `.docx` read and write | `[ ]` |
| 7 | `.xlsx` read and write | `[ ]` |
| 8 | `.pptx` build | `[ ]` |
| 9 | PDF generation | `[ ]` |
| 10 | PDF form fill | `[ ]` |
| 11 | OCR (optional extra) | `[ ]` |
| 12 | spreadsheet formulas as data | `[ ]` |
| 13 | redaction | `[ ]` |

### Phase 2 — reranking

| # | part | status |
|---|---|---|
| 14 | the reranker seam | `[x]` | [part-14](part-14-rerank-seam.md) |
| 15 | a lexical reranker (no model) | `[x]` | [part-15](part-15-lexical-rerank.md) |
| 16 | an optional cross-encoder (ONNX) | `[x]` | [part-16](part-16-cross-encoder.md) — built, **not adopted**: 755 MB/batch
| 17 | the honest comparison, and the decision | `[x]` | [part-15](part-15-lexical-rerank.md), [part-16](part-16-cross-encoder.md) — lexical opt-in, cross-encoder not adopted |

### Phase 3 — incremental indexing

| # | part | status |
|---|---|---|
| 18 | delta segments | `[x]` | [part-18](part-18-delta-index.md) — **24× faster than a rebuild** |
| 19 | upsert and delete | `[x]` | [part-19](part-19-upsert-delete.md) |
| 20 | background compaction | `[x]` | [part-20](part-20-compaction.md) |
| 21 | versioned snapshots | `[x]` | [part-21](part-21-snapshots.md) |

### Phase 4 — graph

| # | part | status |
|---|---|---|
| 22 | entity extraction from prose | `[x]` | [part-22](part-22-prose-entities.md) |
| 23 | relation extraction with evidence | `[x]` | [part-23](part-23-relations.md) |
| 24 | community detection | `[x]` | [part-24](part-24-communities.md) |
| 25 | multi-hop expansion | `[x]` | [part-25](part-25-paths.md) |
| 26 | bounded graph ranking | `[x]` | [part-26](part-26-graphboost.md) — **off**: nothing to boost |

### Phase 5 — compaction

| # | part | status |
|---|---|---|
| 27 | compaction at 85% | `[ ]` |
| 28 | verifiable summaries with `seq` ranges | `[ ]` |
| 29 | offload past 20,000 tokens | `[ ]` |
| 30 | anticipation | `[ ]` |

### Phase 6 — orchestration

| # | part | status |
|---|---|---|
| 31 | L1 → L2 → L3 | `[ ]` |
| 32 | remote agents | `[ ]` |
| 33 | `GET /run/<id>` and the critical path | `[ ]` |
| 34 | the caps, enforced | `[ ]` |

### Phase 7 — the review's gaps

| # | part | status |
|---|---|---|
| 35 | JS-rendered pages (optional) | `[ ]` |
| 36 | image text | `[ ]` |
| 37 | multimodal metadata | `[ ]` |

### Phase 8 — hardening

| # | part | status |
|---|---|---|
| 38 | injection detection across ingest | `[ ]` |
| 39 | fuzzing HTML / PDF / CSV / JSON-LD | `[ ]` |
| 40 | the honest comparison table | `[ ]` |

## The recheck, every time

Before starting a new part, the previous work is re-checked:

1. `ruff` for the bug classes that matter — undefined names, shadowed
   definitions, unused assignments (an unused variable often means a value was
   computed and dropped).
2. Every command run by hand on a fresh store, including the empty-store path.
3. The full test suite, and **the warnings** — wave 35's `fork()` warning was
   the only signal of a real deadlock risk.
4. A read of the module the new part will touch.

Findings go in the new part's file under "found while rechecking", because a bug
in old code is part of the new part's cost.

## The template

Every `part-NN-*.md` answers the same five questions:

```markdown
## What was built
## What was pending
## Errors found and fixed   (how it was found, not just what it was)
## What is next
## Checklist for this part
```

## The recheck log

Every recheck, and what it found. A recheck that finds nothing is still recorded,
because "we looked and it was fine" is a different statement from "we did not
look".

| when | scope | found |
|---|---|---|
| before Part 2 | `ruff` on the bug classes that matter, every command, the empty-store path | 1 unused assignment in `pdf.py` that meant positioning ignored the text block; `evaluate` scored an empty store instead of refusing |
| before Part 2 | packaging | the new `eval_sets/` directory was missing from `package-data`, so the set would not have shipped |
| before Part 4 | `ruff`, the set loader, a full `evaluate` run | nothing — clean |
| before Part 14 | `ruff`, the 40-question run, the set loader | nothing — clean |
| before Part 15 | `ruff`, the 40-question run, the benchmark inventory | 39 modules had no benchmark — now measured or exempted with a reason |
| before Part 16 | `ruff`, the 40-question run | the reranker was handed all 60 candidates, not the top of the list |
| before Part 18 | `ruff`, the 40-question run, the benchmark table | nothing — clean |
| before Part 19 | `ruff`, the delta tests | nothing — clean; the bugs came *from* Part 19's own tests |
| before Part 20 | the full benchmark run | **`retrieval` did not record its embedder** — recall@5 read 0.47 (hash fallback) where model2vec gives 0.70 |
| before Part 21 | `ruff`, the delta and snapshot tests | nothing — clean |

## The benchmark list

`bench/registry.py` holds every benchmark, and `python -m bench.registry` runs
them all. `tests/test_benchmarks.py` fails CI when a module has neither a
benchmark nor a written reason.

| benchmark | what it measures |
|---|---|
| blackboard | append, delta read, replay |
| claims | claim arbitration and summary |
| registry | register, heartbeat, sweep at 64 slots |
| address | the address ranking, warm |
| rerank | the second stage over 20 candidates |
| retrieval | a whole query, and its quality |
| ingest | documents to chunks and vectors |
| extract | fixtures to typed tables |
| serve | one request through the JSON API |
| index_delta | append against a full rebuild |
