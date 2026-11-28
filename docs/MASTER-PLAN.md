# Master plan: where this project goes next

Three inputs: an audit of what exists (wave 31), the state of the field in late
2026, and the agent fabric designed in `docs/AGENTS.md`. This document lists the
skills, compares the old plan with the new evidence, and merges them into one
plan.

---

## Part A — the landscape, and what it actually says

Read from each leaderboard's own data in October–November 2026. The numbers
matter less than the pattern underneath them.

| benchmark | top score | human baseline | state |
|---|---|---|---|
| OSWorld-Verified (computer use) | 90.19% | 72.36% | **saturated** |
| WebArena (browser) | 86.1% | 78.24% | **saturated** |
| GAIA test set (search + tools) | 94.0% | 92% | **saturated** |
| τ²-bench text (customer service) | 87.9% | — | near ceiling |
| SWE-bench Verified (coding) | 79.2% | — | **contaminated** |
| SWE-bench Pro (coding, longer) | 61.5% ± 3.1 | — | ~30% of tasks flagged broken |
| Terminal-Bench 4.0 (terminal) | 64.85% ± 3.11 | — | active |
| τ³-Banking (policy + retrieval) | 55.2% | — | active |
| OSWorld 2.0 (long workflows) | **20.6%** | — | **unsolved** |
| METR time horizon (50% success) | ~12 hours | — | active |

Three findings that should change how this project spends its effort:

**1. The old benchmarks are finished as ranking tools.** OSWorld-Verified,
WebArena, GAIA and τ²-bench now sit at or above the human baseline. A score above
human on a set that has been public since 2023 is not evidence of super-human
ability; it is evidence that the benchmark is saturated.

**2. SWE-bench Verified was withdrawn by its most prominent reporter.** In
February 2026 OpenAI stopped reporting it after auditing 138 problems: 59.4% had
flawed tests or descriptions, and every frontier model could reproduce gold
patches — contamination. An independent audit (SWE-Bench+) had found 32.67% of
successful patches had the solution in the issue text. The same audit pattern hit
SWE-bench Pro: about 30% of tasks are broken.

**3. The frontier moved to long horizons, reliability and cost.** OSWorld 2.0 —
108 workflows averaging 1.6 human-hours and 250+ agent steps — is at 20.6%. METR's
time-horizon curve is the metric that still separates systems.

### What this means for `vrusha`

- **Do not chase a saturated benchmark.** Being good at GAIA tells nobody
  anything in 2026.
- **Build for long horizons and reproducibility**, which is what the store, the
  licence gates and `verify --refetch` are for. Provenance is the thing a
  long-running agent needs and a one-shot benchmark does not test.
- **Report cost per completed task beside every quality number**, because that is
  where the field now differentiates.

---

## Part B — context management: what the field converged on

Five named primitives have emerged (arXiv 2607.21503), and every production
harness implements some subset:

| primitive | what it means | what this project already has |
|---|---|---|
| **architecting** | decide the shape of memory before storing anything | `docs/ARCHITECTURE.md`, the address tree |
| **ingesting** | raw signal to retrievable memory — *retrieval quality is bounded by ingestion quality* | `extract.py`, `records.py`, `jsonld.py`, `pdf.py`, `formula.py` |
| **scoping** | which slice of everything is relevant, with strict isolation | the address tree; **not** multi-tenant isolation |
| **anticipating** | prefetch what the agent will need next | **missing** |
| **compacting** | reduce under budget without losing what matters | **missing** |

Concrete thresholds other systems use, which are worth adopting rather than
inventing:

| mechanism | threshold used elsewhere | our equivalent |
|---|---|---|
| offload a tool result to storage, leave a pointer | > 20,000 tokens | the blackboard `refs` design |
| summarise / compact | at ~85% of the window | not built |
| subagent isolation | child never inherits the parent transcript | `docs/AGENTS.md` §3 |
| keep recently touched files | the 5 most recent | retrieval-on-demand instead |

The single most useful sentence from that literature for this project:
**"retrieval quality is bounded by ingestion quality."** That is exactly why the
extraction waves (9–16) were worth the effort, and why the next phase should not
start by adding agents.

---

## Part C — the skills inventory

Adopting the taxonomy the field has settled on: a skill is **domain × operation ×
modality**, and the three are not interchangeable. `document-editing` and
`code-translation` are *operations*, not domains — treating them as domains is how
a skill catalogue becomes a pile of 300 overlapping tags.

**Status** column: `have` = built and tested here; `part` = partial; `gap` = not
built. **Offline** = can it run with no network.

### 1. Retrieval and knowledge (14)

| skill | operation | modality | offline | status |
|---|---|---|---|---|
| web search | search | webpage | no | **have** (`search.py`) |
| page fetch with charset + caps | extraction | webpage | no | **have** (`fetch.py`) |
| boilerplate removal | transformation | webpage | yes | **have** (trafilatura, optional) |
| structure-aware chunking | transformation | text | yes | **have** (`chunk.py`) |
| embedding | classification | text | yes | **have** (model2vec) |
| hybrid retrieval (BM25 + vector, RRF) | ranking | text | yes | **have** |
| approximate nearest neighbour | ranking | vector | yes | **have** (IVF + sqlite-vec) |
| table extraction | extraction | html, csv | yes | **have** |
| PDF text and tables | extraction | pdf | yes | **have** |
| JSON-LD / schema.org reading | extraction | webpage | yes | **have** |
| formula storage and matching | search | text, latex | yes | **have** |
| cross-table relation inference | analysis | table | yes | **have** |
| entity resolution (fuzzy) | classification | table | yes | **have** |
| citation verification by refetch | verification | webpage | no | **have** (`verify --refetch`) |

### 2. Documents and office (8)

| skill | operation | modality | offline | status |
|---|---|---|---|---|
| read/write .docx | generation | document | yes | **gap** |
| read/write .xlsx | generation | spreadsheet | yes | **gap** |
| build .pptx | generation | presentation | yes | **gap** |
| generate PDF | generation | pdf | yes | **gap** |
| fill PDF forms | transformation | pdf | yes | **gap** |
| OCR of scanned pages | extraction | image, pdf | yes | **gap** |
| spreadsheet formulas and pivots | calculation | spreadsheet | yes | **gap** |
| redaction of personal data | transformation | document | yes | **gap** |

The four document formats are the single largest missing block, and each is one
library away. They are also the most commonly requested agent capability in every
catalogue surveyed.

### 3. Media and content production (12)

| skill | operation | modality | offline | status |
|---|---|---|---|---|
| speech recognition | extraction | audio | part | via **dubforge** |
| speaker diarisation | classification | audio | part | via **dubforge** |
| translation for dubbing | transformation | audio, text | part | via **dubforge** |
| speech synthesis | generation | audio | part | via **dubforge** |
| voice cloning (consented) | generation | audio | part | via **dubforge** |
| forced alignment | analysis | audio, text | part | via **dubforge** |
| audio mix | transformation | audio | part | via **dubforge** |
| subtitle generation | generation | video | yes | **gap** |
| video cut to intent | transformation | video | — | **cutforge: spec only** |
| image understanding | classification | image | yes | **gap** |
| image generation / editing | generation | image | no | **gap** |
| 3D create / repair / render | generation | 3d-model | part | via **forge3d** |

### 4. Software engineering (8)

| skill | operation | modality | offline | status |
|---|---|---|---|---|
| repository reading and indexing | extraction | source-code | yes | **have** |
| code search with identifiers | search | source-code | yes | **have** |
| patch generation | generation | source-code | no | **gap** |
| test repair | repair | source-code | no | **gap** |
| dependency repair | repair | source-code | no | **gap** |
| static analysis / linting | verification | source-code | yes | **gap** |
| benchmark harness for agents | verification | source-code | yes | **gap** |
| documentation generation | generation | source-code | yes | **gap** |

### 5. Data and analysis (7)

| skill | operation | modality | offline | status |
|---|---|---|---|---|
| CSV/Excel analysis | analysis | csv, spreadsheet | yes | **part** (`derive`) |
| SQL over the store | search | database | yes | **have** (SQLite) |
| derived columns from checked formulas | calculation | table | yes | **have** (`formula.py`) |
| scripted computation in a sandbox | calculation | table | yes | **have** (`sandbox.py`) |
| chart generation | generation | image | yes | **gap** |
| time-series analysis | analysis | time-series | yes | **gap** |
| geospatial data | analysis | geospatial | yes | **gap** |

### 6. Language (7)

| skill | operation | modality | offline | status |
|---|---|---|---|---|
| tokenisation (BPE) | transformation | text | yes | **have** |
| Indic script normalisation | transformation | text | yes | **have** (20 profiles) |
| translation | transformation | text | no | **part** (via dubforge) |
| transliteration | transformation | text | yes | **gap** |
| summarisation | transformation | text | no | **part** (`answer.py`) |
| glossary / terminology consistency | verification | text | yes | **gap** |
| style and register checking | verification | text | yes | **gap** |

### 7. Agent infrastructure (8)

| skill | operation | modality | offline | status |
|---|---|---|---|---|
| task decomposition | planning | text | no | **gap** |
| tool routing with capability gates | planning | — | yes | **have** (`router.py`) |
| resource-aware scheduling | planning | — | yes | **have** (`backends.py`) |
| context compaction | transformation | text | yes | **gap** |
| long-term memory | transformation | text | yes | **part** (the store) |
| anticipatory prefetch | planning | text | yes | **gap** |
| cost accounting per task | analysis | — | yes | **gap** |
| run observability and traces | analysis | — | yes | **part** (`report.py`) |

### 8. Safety and verification (6)

| skill | operation | modality | offline | status |
|---|---|---|---|---|
| licence gate | verification | webpage | yes | **have** (`rules/`) |
| robots and rate limiting | verification | webpage | yes | **have** (`net.py`) |
| provenance and drift detection | verification | webpage | no | **have** (`verify`) |
| prompt-injection detection | detection | text | yes | **gap** |
| PII detection and redaction | detection | text | yes | **gap** |
| contradiction detection across sources | verification | text | yes | **gap** |

**Totals: 70 skills — 26 have, 14 partial, 30 gaps.** (Nine of the partial ones
are the media skills reached through the `dubforge` and `forge3d` adapters rather
than built here; that distinction matters when deciding what to build next.)

The gaps cluster in three places: **documents/office** (8 of 8 missing — nothing
in this group exists), **media** (3 outright, 9 only through a tool that has to be
installed), and **agent infrastructure** (4: decomposition, compaction,
anticipation, cost accounting). That is the shape of the next phase, and it is not
the shape the previous plan assumed.

---

## Part D — old plan vs new plan

### What the old plan said

`docs/PROGRAM.md` (six parts, all done) and `docs/AGENTS.md` (ten steps, designed):

1. math layer · 2. research loop · 3-5. three tools · 6. repo survey
2. agent fabric: blackboard → claims → registry → skills → partition →
   orchestrate → remote → observe → measure → injection test

### What the evidence changed

| # | the old assumption | what the evidence says | the change |
|---|---|---|---|
| 1 | "more agents will make it faster" | 64 windows is 34–275 GB of KV cache; the field's own harnesses isolate rather than share | keep the design, but **step 1 (blackboard) first**, and treat 8 workers as the likely optimum |
| 2 | "benchmarks tell us where we stand" | the public suites are saturated, and the two most-cited coding benchmarks are contaminated | **do not adopt an external benchmark**; build a long-horizon task set of our own, with cost per task |
| 3 | "skills are a list we harvest" | skills have a settled taxonomy: domain × operation × modality | adopt it; it is also **exactly the 64-bucket partition** the fabric needs |
| 4 | "context management is a feature" | it is five named primitives, and *retrieval quality is bounded by ingestion quality* | ingestion is done; **compaction and anticipation are missing** and now have thresholds to hit (85%, 20k) |
| 5 | "the gaps are in extraction" | extraction is complete (30 skills built); the gaps are documents, media, and agent infrastructure | **reorder the phases**: documents and compaction before orchestration |
| 6 | "one plan, executed in order" | the audit found 4 bugs in code that shipped 20 waves ago | every phase keeps its acceptance test, and each phase **starts with an audit of the phase before it** |

### The honest difference in size

The old plan was 6 parts and roughly 20 waves. The new one is **5 phases and
about 40 steps**, because the skill inventory is 32 gaps wide rather than 6. It is
also more sequential: phase 2 (documents + compaction) unblocks phase 3
(orchestration), and skipping it would build 64 agents on a foundation with no
compaction.

---

## Part E — the merged plan

### Phase 0 — the foundation is audited *(done, wave 31)*

Acceptance: `docs/AUDIT-2.md` exists, four bugs fixed, the non-bug documented.

### Phase 1 — the blackboard *(agent steps 1–3)*

Append-only entries with a monotonic `seq`; claims with leases; a registry of 64
slots.

**Acceptance:** 1,000 parallel appends produce 1,000 distinct `seq` values and lose
none; 64 agents racing for 10 tasks produce exactly 10 winners; a killed agent's
slot goes `dead` within 2× heartbeat.

**Why first:** it is the substrate for everything else, and it is useful even if
the four-level hierarchy is later replaced by a scheduler.

### Phase 2 — documents, compaction, and the two missing primitives

**2a. Documents (8 skills).** `.docx`, `.xlsx`, `.pptx`, PDF generation, PDF forms,
OCR, spreadsheet formulas, redaction. Each behind the same lazy-import rule as the
tools: `import vrusha` must not gain a dependency.

**2b. Compaction.** At 85% of the window, summarise to a structured state —
decisions, unresolved problems, artifact references — and write the full text to
the blackboard. **Compaction must be verifiable:** the summary carries the `seq`
range it replaced, so a lost fact is traceable.

**2c. Anticipation.** Prefetch the next task's likely context while the current one
runs, off the critical path.

**Acceptance:** a document round-trips through each of the eight formats with
formatting intact; a 40-turn transcript compacts to under 15% of its tokens while
retaining every decision (checked by a fixture of planted facts); a prefetch either
hits or costs under 5 ms.

**Why before orchestration:** 64 agents with no compaction is 64 agents that each
run out of window. This is the phase the old plan was missing.

### Phase 3 — skills: harvest, classify, partition *(agent steps 4–5)*

Three sources, ranked (agent skills → MCP servers → repos). Every record carries
**domain, operation, modality, licence, offline, requires, cost** — the taxonomy in
Part C, not free-form tags.

Partition into 64 buckets by domain: 8 domains × 8 skills is the natural shape, and
it keeps each agent's prompt short.

**Acceptance:** 640 fixture skills land in 64 buckets, none empty, max/min ratio
≤ 2, each exactly once; a GPL item is refused with the reason recorded.

### Phase 4 — orchestration and remote agents *(agent steps 6–8)*

L1 super, ≤4 admins, ≤16 parents, ≤64 live. Remote agents speak the same protocol.

**Acceptance:** a 12-task goal completes; a 13th fan-out is refused, not queued; a
parent that dies has its task reclaimed and finished; a remote agent's finding is
indistinguishable from a local one; a 20-task run reports its critical path.

### Phase 5 — evaluation, on our own terms

Not GAIA. A **long-horizon task set** of our own, because that is where the field's
frontier actually is (OSWorld 2.0: 20.6%) and because a saturated benchmark cannot
measure what we are building.

**The set:** 20 tasks that each need 20+ steps, real sources, and a verifiable
answer. Scored on: task completion, **cost per completed task**, wall clock, and
whether every claim traces to a stored source.

**Acceptance:** the suite runs 1 / 4 / 8 / 16 / 32 / 64 agents on the same tasks and
reports the number at which adding agents stops helping — with the honest answer
allowed to be 8.

### Phase 6 — hardening *(the gaps `docs/AUDIT-2.md` named)*

Concurrency stress, adversarial input (fuzzed HTML/PDF/CSV), prompt-injection
detection, PII redaction, and the injection test from `docs/AGENTS.md` step 10.

**Acceptance:** two processes writing concurrently never lose an entry; a fuzzed
PDF never raises an unhandled exception; an injected instruction in fetched content
does not execute and is marked `quarantined`.

---

## Part F — what this plan refuses

| refused | why |
|---|---|
| adopting GAIA / SWE-bench as our score | saturated, and two of them are contaminated; a number nobody trusts is not a target |
| building 64 agents before compaction | they would each run out of window; phase 2 exists for this |
| free-form skill tags | the field settled on domain × operation × modality, and free tags produce 300 overlapping labels |
| a framework for the agent layer | the store, gates and address tree are the value (`docs/SURVEY.md`) |
| claiming speed without the measurement | phase 5 reports cost per completed task, and the answer may be "fewer agents" |

## Part G — the three risks that decide this

1. **Compaction is where correctness dies.** A summary that silently drops a fact
   produces confident wrong answers. Mitigation: every summary carries the `seq`
   range it replaced, and a planted-fact fixture tests retention.
2. **The model.** Whether a local model can drive L1/L2 planning while L4 runs
   skills is unknown and is the biggest single risk (`docs/MODEL.md`).
3. **Four levels may not beat one.** Step 9 / phase 5 answers it with numbers. If
   a single agent with good retrieval wins, the plan says so and the hierarchy
   becomes a scheduler.
