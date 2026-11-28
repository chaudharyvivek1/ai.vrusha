# Plan V3: the review, the repo, and a deeper plan than either

Three inputs, compared honestly:

1. **The ChatGPT review** (28 November 2026) — an outside assessment with a score
   per area and a roadmap.
2. **The repository as it stands** — wave 35, 542 tests, one branch.
3. **The two plans already written** — `docs/MASTER-PLAN.md` (six phases) and
   `docs/AGENTS.md` (the agent fabric, steps 1–3 built).

This document says where the review is right, where it is already out of date,
where it is wrong, and what the merged plan is. It is longer and more divided than
`MASTER-PLAN.md` because the review found two things that plan had missed.

---

## Part A — what the review says

| area | score | its verdict |
|---|---|---|
| Original architecture | 9/10 | the address system is the distinguishing technology |
| Governance design | 9/10 | rules, provenance, licensing |
| Vector indexing | 8/10 | "keep developing it; do not replace it with a vector database" |
| Retrieval foundation | 7/10 | hybrid retrieval exists; embeddings and reranking are the gap |
| Graph intelligence | 5/10 | nearest-neighbour edges are not a knowledge graph |
| Production readiness | 4/10 | no incremental indexing, no eval harness |
| Document intelligence | 3/10 | "PDF, DOCX, XLSX, PPTX, OCR" |
| Custom LLM maturity | 2/10 | do not make training it the priority |
| **Overall** | **7.5/10** | "architectural potential, not a measured score" |

Its roadmap: **P0** semantic embeddings + reranking + RAG evaluation · **P1** PDF/
OCR/documents, incremental ANN, knowledge graph + multi-hop · **P2** query
planning, multimodal, production API · **P3** model training.

Its sharpest line, and the one worth keeping:

> "You should not spend months training a tiny language model while semantic
> retrieval, document parsing and evaluation remain incomplete."

---

## Part B — the review against the repository

The review is partly current — it cites the wave-24 real-corpus benchmark — and
partly not. Point by point, against `main` at wave 35:

| review claim | repo state | evidence |
|---|---|---|
| **hash embedder is the default** (critical) | **closed, wave 25** | `store.embed_backend: model2vec`; measured: `bm25+hash` scored 8% recall@1, *worse* than bm25 alone |
| **no reranking** (critical) | **open** | only `_identifier_bonus`; no second stage at all |
| **RAG evaluation** (critical) | **partly** | `bench/embedders.py` has recall@1/@3 and MRR; no nDCG, no citation accuracy, no faithfulness |
| **PDF ingestion missing** | **partly closed, wave 12** | `store/pdf.py` reads positioned text and tables with `zlib` and no dependency; **OCR is still missing** |
| **DOCX/XLSX/PPTX** | **open** | all 8 document skills in the inventory are missing |
| **images / multimodal** | **open** | nothing |
| **graph is only NN edges** | **partly closed, waves 14 + 26** | `relations.py` infers joins; `entities.py` resolves spellings with a score; **community detection and multi-hop are missing** |
| **no incremental indexing** | **open** | `ann.build` rewrites the index; a stale index is rebuilt, not updated |
| "249 unit tests" | **542 now** | `tests/` |
| "do not train a model first" | **agreed** | `docs/MODEL.md` exists; the plan puts it last |
| **JavaScript-rendered pages missing** | **open, and it is right** | `fetch.py` has no browser; this is a real gap the review found and my plan had understated |

### Where the review changed my mind

**1. Evaluation datasets are not leaderboards.** `MASTER-PLAN.md` said "do not
adopt an external benchmark". That was aimed at *saturated leaderboards* — GAIA,
SWE-bench — and it was too broad. BEIR and CRAG are **datasets**: a fixed set of
queries, documents and relevance labels. Using a subset is legitimate, cheap and
comparable. Adopting a dataset is not chasing a leaderboard.

**2. Reranking was under-weighted.** My plan treated it as a "part" of retrieval.
The review is right that it is a *critical* stage: fusing BM25 and vectors gets
the right chunk into the top 20; a reranker is what puts it at rank 1. The measured
numbers support this — 75% recall@3 for `bm25+model2vec` still means one query in
four has the answer at rank 4 or worse.

### Where I disagree, with reasons

| review says | I say | why |
|---|---|---|
| "study Docling, MinerU, RAGFlow DeepDoc" | adopt the *ideas*, not the packages | `docs/SURVEY.md` measured the cost: torch in the core. Layout and OCR can be optional extras behind the same lazy rule as the tools |
| "cross-encoder reranking" as a P0 | P0 **after** evaluation exists | a reranker's benefit is unmeasurable without an eval set, and every cross-encoder pulls torch or ONNX. Measure first, then add the seam, then the model |
| "PDF and complex documents: Gap" | PDF text and tables shipped in wave 12 | the review is reading an older README; what is missing is OCR and Office formats |
| "production API and observability: P2" | it is **P1** | `vrusha serve` already exists and the blackboard needs routes; observability is what makes 64 agents debuggable |
| overall 7.5/10 | **not scored** | a number without a method is not a number, which is the convention this repo already holds itself to |

---

## Part C — the three plans compared

| question | `MASTER-PLAN.md` | `AGENTS.md` | the review | merged |
|---|---|---|---|---|
| what is missing most? | documents + compaction | nothing (it is a design) | reranking + graph + eval | **evaluation first, then documents, then reranking** |
| agent fabric | phase 1–4 | the whole thing | "agentic RAG: gap" | keep, and finish it after the measurement exists |
| documents | phase 2a | — | critical | phase 1, in the same order |
| reranking | not mentioned | — | critical | **phase 2, added** |
| incremental indexing | not mentioned | — | high | **phase 3, added** |
| graph | "passes.py already does it" | — | 5/10, needs communities + multi-hop | **phase 4, added** |
| compaction | phase 2b | §3, §9 | not mentioned | phase 5 |
| orchestration | phase 4 | steps 6–8 | "agentic RAG gap" | phase 6 |
| evaluation | phase 5, own task set | step 9 | BEIR/CRAG/Ragas | **phase 0 — first, and datasets allowed** |
| JS rendering | understated | — | found it | phase 7 |
| model training | not a phase | — | "later" | not a phase |

**The three blind spots, one per source:**

- **The review missed compaction entirely.** With 64 agents it is the load-bearing
  feature; without it each agent runs out of window.
- **My master plan missed reranking and incremental indexing**, both of which the
  review is right about.
- **The agent design assumed retrieval was good enough to route on.** The
  measured numbers say it is not yet — 75% recall@3.

**The merge, in one sentence:** *measure first, because everything else is a guess
until retrieval quality is a number; then fix documents and reranking, because
they are what the measurement will show is broken.*

---

## Part D — the plan, in forty parts

Every part has: what it builds, and **an acceptance test that fails today**. After
each part there is a **review checkpoint** — re-read the module, run the audit,
and decide whether the next part is still the right one. That discipline is not
decoration: waves 31 and 35 both found bugs in code that had already shipped.

### Phase 0 — measurement (Parts 1–5)

Nothing else in this plan can be judged without it.

| part | builds | acceptance |
|---|---|---|
| **1** | `vrusha/evaluate.py`: recall@k, precision@k, MRR, **nDCG@k**, citation accuracy, faithfulness proxy | a hand-labelled 12-query fixture gives the same numbers as a hand calculation |
| **2** | an evaluation set: 40 questions over this repo's own documents, with the right document and the right passage labelled | the set loads, every question has a label, and the loader refuses a malformed label |
| **3** | `vrusha evaluate --set docs` — runs the current pipeline and prints every metric with its definition | one command, one table, reproducible to the digit |
| **4** | cost accounting: tokens, seconds and bytes per answered question | a run reports cost per completed question, not per token |
| **5** | a BEIR/CRAG **subset loader** (optional download, cached, never in CI) | a small subset loads and scores; with no network the loader says so and skips |

**Review checkpoint 0:** is retrieval quality what the review assumed? The answer
decides whether Phase 2 (reranking) comes before Phase 1 (documents).

### Phase 1 — documents (Parts 6–13)

| part | builds | acceptance |
|---|---|---|
| **6** | `.docx` read and write | a document round-trips with headings, tables and bold intact |
| **7** | `.xlsx` read and write | a sheet round-trips with types intact; a formula is preserved as a formula |
| **8** | `.pptx` build | a deck with a title slide, bullets and a table opens |
| **9** | PDF generation | text, tables and page numbers; no dependency added to the core |
| **10** | PDF form fill | a filled field is readable by the same PDF reader that wrote it |
| **11** | OCR (optional extra) | a scanned page yields text; without the extra installed, the command says what to install |
| **12** | spreadsheet formulas as data | `=SUM(B2:B9)` is read as a formula, never evaluated |
| **13** | redaction | a named entity is replaced everywhere, and the report says how many |

Every one follows the existing rule: **lazy import**, so `import vrusha` gains no
dependency.

### Phase 2 — reranking (Parts 14–17)

| part | builds | acceptance |
|---|---|---|
| **14** | a reranker **seam** — a protocol, a no-op default, and a registry | the pipeline is identical with the no-op installed |
| **15** | a **lexical** reranker that needs no model: field-weighted BM25 over the top 20, with the title and symbol weighted | nDCG@10 improves on the Phase 0 set, or the part is reverted |
| **16** | an **optional** cross-encoder via ONNX (not torch) | measured on the same set; the extra is refused when not installed |
| **17** | the honest comparison: no-op vs lexical vs cross-encoder, with cost | a table; if the cross-encoder does not beat lexical by a margin, it is not adopted |

### Phase 3 — incremental indexing (Parts 18–21)

| part | builds | acceptance |
|---|---|---|
| **18** | append-only delta segments: a new vector joins a small segment instead of forcing a rebuild | adding 1,000 vectors does not rewrite the 200,000 that were there |
| **19** | upsert and delete by chunk id | a changed chunk is replaced, not duplicated; a deleted chunk stops appearing |
| **20** | background compaction: segments merge when they outnumber the base | the index size stays within 1.5× of a fresh build |
| **21** | versioned snapshots + concurrent readers | a reader holding a snapshot keeps reading while a writer adds |

### Phase 4 — graph (Parts 22–26)

| part | builds | acceptance |
|---|---|---|
| **22** | entity extraction from text, not only tables | names, organisations and places from prose, with spans |
| **23** | relation extraction with evidence | every edge carries the chunk that asserted it |
| **24** | **community detection** (label propagation, no dependency) | communities on a known graph match the hand-computed answer |
| **25** | **multi-hop** expansion in retrieval | a two-hop question answers from two chunks; the hop is visible in the trace |
| **26** | graph-aware ranking: a chunk in the answer's community gets a bounded boost | measured on the Phase 0 set; bounded, never dominant |

### Phase 5 — compaction and anticipation (Parts 27–30)

| part | builds | acceptance |
|---|---|---|
| **27** | compaction at 85% of a budget | a 40-turn transcript compacts under 15% of its tokens |
| **28** | **verifiable** summaries: every summary carries the `seq` range it replaced | a planted fact survives compaction, or the summary is rejected |
| **29** | offload past 20,000 tokens to the blackboard, leave a reference | the working set stays under budget and the reference resolves |
| **30** | anticipation: prefetch the next task's likely context | a hit is used; a miss costs under 5 ms |

### Phase 6 — orchestration (Parts 31–34)

| part | builds | acceptance |
|---|---|---|
| **31** | L1 → L2 → L3 over board + claims + registry | a 12-task goal completes with ≤ 64 live agents |
| **32** | remote agents over the same protocol | a remote finding is indistinguishable from a local one |
| **33** | `GET /run/<id>` and `vrusha run` — the tree, states, critical path | a 20-task run shows its longest chain and idle time |
| **34** | the caps, enforced at spawn, with the refusal message | a 13th fan-out is refused, not queued |

### Phase 7 — the gaps the review found (Parts 35–37)

| part | builds | acceptance |
|---|---|---|
| **35** | JS-rendered pages: an **optional** headless render behind the same seam as the tools | a page that needs JavaScript yields text when the extra is installed, and says so when it is not |
| **36** | image text (OCR in the fetch path) | an image-only page is either read or refused with a reason |
| **37** | multimodal metadata: an image is a chunk with a caption and a source | the store can answer "which image" without pretending to see it |

### Phase 8 — hardening and honesty (Parts 38–40)

| part | builds | acceptance |
|---|---|---|
| **38** | injection detection across every ingest path | a planted instruction is quarantined, and the run completes |
| **39** | fuzzing: HTML, PDF, CSV, JSON-LD | 10,000 mutated inputs, no unhandled exception |
| **40** | the honest comparison table: vrusha vs a plain baseline on the Phase 0 set | numbers, cost, and a paragraph on what the numbers do not show |

---

## Part E — the discipline this plan runs on

The user's instruction, made into a rule: **after every part, review the old
code.** Concretely, every part ends with four steps:

1. **Re-read the module it touched**, top to bottom, before writing the next one.
2. **Run the audit**: `ruff` on the bug classes that matter (undefined names,
   shadowed definitions, mutable defaults), every command by hand, and the edge
   paths — empty store, missing table, no network.
3. **Check the tests for the shape, not the speed.** Both recent bugs
   (`sqlite3` connection shared across threads; a write locked but the read not)
   were invisible to assertions and obvious to a profile.
4. **Ask whether the next part is still right.** Phase order is a hypothesis, and
   Phase 0 exists to test it.

Recorded in `CHANGELOG.md` with the bug each part found, which is what the last
35 waves have done.

---

## Part F — what this plan refuses

| refused | why |
|---|---|
| a leaderboard score as the goal | saturated and contaminated (`MASTER-PLAN.md` Part A); datasets yes, targets no |
| torch in the core | the package's only hard dependency stays numpy |
| a cross-encoder before an eval set | unmeasurable benefit, guaranteed cost |
| rebuilding the index on every write | Phase 3 exists because the review is right about this |
| four levels of agents before retrieval is measured | Phase 0 first, Phase 6 last |
| scoring the project out of 10 | a number without a method is not a number |

## Part G — the three risks

1. **Phase 0 may show retrieval is fine** — in which case Phases 1–4 reorder and
   reranking drops to Phase 5. That is a success, not a wasted phase.
2. **The cross-encoder may not beat the lexical reranker.** Then it is not
   adopted, and Part 17 records why.
3. **Compaction is where correctness dies** (unchanged from `MASTER-PLAN.md`): a
   summary that silently drops a fact produces confident wrong answers, which is
   why Part 28 makes summaries carry their provenance.
