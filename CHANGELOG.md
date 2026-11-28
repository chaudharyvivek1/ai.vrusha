# Changelog

Every change that landed on `main`, in the order it landed, with what it did and
what it cost. Waves 1–5 predate this file; they are the harvest, address, passes,
gate and model layers the rest builds on.

Format: **what changed** · **new files** · **the bug the tests or the demo found**.

---

## Wave 21 — does the vector mean anything? · PR #17

Re-ran retrieval quality with the real embedder, on labelled queries, and the
answer changes the conclusion of wave 20.

- `bench/embedders.py` — 15 documents from `docs/`, 12 hand-written queries that
  avoid the target's own words; bm25 vs hash vs model2vec, at document and at
  chunk granularity, plus RRF fusion as `retrieve()` runs it
- `docs/BENCHMARKS.md` — a new section with all three tables and the caveats

**The numbers.** One vector per document: bm25 17% recall@1, hash 17%,
**model2vec 33%**. Chunked at 500 characters: bm25 **25%**, hash 17%,
model2vec 17%, and fused — bm25+hash 8%, **bm25+model2vec 25% recall@1 / 75%
recall@3 / MRR 0.524**.

**Three findings.** At chunk level keywords win alone, because a 500-character
chunk still contains the words. Fusion is where semantics pays: +17 points of
recall@3 over BM25 alone. And **the old default was actively harmful** — bm25+hash
scores 8% recall@1, worse than BM25 alone, because a lexical hash is a second
copy of the same evidence and RRF double-counts it.

**Bugs in the harness, before the numbers could be trusted:** FTS5 reads a bare
multi-word query as AND, so the first BM25 baseline scored 0% and was a broken
comparison rather than a weak one; and a per-query record keyed by a list raised
`TypeError`.

---

## Wave 20 — survey, and model2vec adopted · PR #16

The AI repo survey, and the one candidate that fixes the biggest gap in the store.

- `docs/SURVEY.md` — six candidates judged on licence, what they add, dependency
  weight and offline testability; a rejected list with reasons (GPL, frameworks,
  torch-heavy PDF stacks)
- `vrusha/store/embed.py` — `Model2VecEmbedder`: static embeddings, ~8 MB, numpy
  inference; dimension read **from the model**, vectors normalised, loaded
  lazily, falls back to `hash` with a printed reason
- `vrusha/config.py` — `store.embed_backend: model2vec`, `embed_model_static`

**Why:** the default embedder was `hash`, so the vector signal could never beat
BM25 — the largest quality gap in the project, visible in retrieval quality, in
the benchmark's isotropic row, and in the advice in `docs/ANN.md`.

**Tested:** dimension discovery, normalisation, the fallback and the aliases,
against a stub module — the hub is not reachable from CI, and a test that needs a
network fails for the wrong reason.

---

## Wave 19 — tools: dubforge, forge3d, cutforge · PR #15

One interface for the three engines, and an honest inventory.

- `vrusha/tools/__init__.py` — `ExternalTool`: found as `module` | `checkout` |
  `cli`, run as a **subprocess** with a whitelisted action list, JSON parsed when
  the tool emits it, timeouts enforced
- `vrusha/tools/{dubforge,forge3d,cutforge}.py` — three registrations
- `vrusha tools`, `vrusha tools NAME [--action] [--option k=v]`
- `docs/TOOLS.md`

**The decision:** the adapter is in-tree, the code is not. Copying dubforge's 83
files would buy drift and pull `trimesh`/`scipy`/`pillow` into a numpy-only
package; one clone, one command and one honest status line is what "merged" needs
to mean.

**What reading the repos found:** `cutforge` is a *specification* — its README
says so — and its LICENSE is "All rights reserved", which cannot be reconciled
with this repository's MIT. It is registered as `mode: specification` and never
available, so the listing tells the truth instead of promising an engine.

---

## Wave 18 — research loop · PR #14

A question in, an answer out, and every source that made it.

- `vrusha/research.py` — `search` → fetch → gate → `ingest` → `extract` → `ask`,
  returning sources as a first-class output
- a source is a URL, title, **licence**, retrieval time, chunk/table/formula
  counts, and `verified` (`unchanged` / `drifted` / `rewritten` / `gone`)
- `vrusha research "question" [--verify] [--licence MIT]`
- `docs/RESEARCH.md`, and this file

**Bugs:** `Answer.hits` is a count, not a list, so the citation loop threw
`TypeError`; a refused page returned zero sources with no explanation; and a
`harvest.min_words` override had **no effect at all** — the gate reads
`rules["quality.min_words"]`. A config key read by nothing is worse than no key.

---

## Wave 17 — math layer · PR #13

Formulae are canonicalised before matching, so two spellings of one expression
are one string.

- `vrusha/store/mathquery.py` — unicode → LaTeX (`∫₀¹`, `x²`, `α`), rendering
  commands dropped, brace-aware `\frac`/`\sqrt`, commutative ordering per side of
  `=`, outer parens stripped, symbols and subformulas, `match()` scoring
  exact > subformula > symbols
- `x_equations` beside the tables, written by `extract`, with document, url and licence
- `vrusha math "e^{i\pi}+1=0"`, `vrusha math` to list
- `docs/PROGRAM.md` (the six-part plan), `docs/MATH.md`

**Bugs:** `=` binds looser than `+`, so ordering the whole expression turned
`sin²+cos²=1` into `cos²=1+sin²`; `\frac` with a nested `\sqrt` did not parse and
left `frac` as a symbol matching everything; collapsing `\)+` ate a legitimate
closing paren; `\left(a+b\right)` canonicalised to `(a+b)`, not `a+b`.
`Costs $5 and $10` was read as two formulae.

---

## Wave 16 — tier-2 scripts · PR #12

Real Python against scraped rows, in a namespace, with the limits stated.

- `vrusha/store/sandbox.py` — user+network namespace (verified `ENETUNREACH`),
  `RLIMIT_CPU/AS/NPROC/FSIZE`, cleared environment, process group killed at the
  timeout, result validated (one row per input row, scalar cells, valid names)
- `x_columns.origin` = `script:<sha256 prefix>`, so a computed value is never
  mistaken for a stated one
- `vrusha derive --script PATH --allow-scripts [--timeout] [--isolation]`

**Not enforced, and documented as such:** filesystem read access, a determined
import escape, and any of it on a kernel without user namespaces.

---

## Fix — a date column is a measure, not a key · PR #11

`normalise()` folds separators, so `2026-11-27` reached the measure check as
`2026 11 27`, the ISO pattern stopped matching, and `launch = renewal` joined at
100% overlap. The pattern matches the folded form.

**Found by:** running `vrusha joins` by hand *after* the wave-14 merge. The tests
were green.

---

## Wave 14 — relation inference · PR #10

Which columns across tables are the same entity, stored with the evidence.

- `vrusha/store/relations.py` — key candidates (unique, non-null, short),
  Jaccard overlap across tables, edges in `x_joins` with `shared` and `overlap`
- `vrusha joins --build`, `vrusha joins`, `vrusha joins --table N`

**Bug:** the first run joined `price` to `price` and `uptime` to `uptime` at 100%
— three distinct prices in a three-row table pass a uniqueness test. A
mostly-numeric column is now excluded unless its name says identity.

---

## Wave 13 — harvest --extract · PR #9

The fetched page is read, not re-fetched.

- `Document.html` kept; `html_to_text(extractor=...)`: `simple` (default) |
  `trafilatura` | `auto`
- `records.extract_document()` — tables and JSON-LD from an in-memory page
- `vrusha harvest --extract [--mdy]`

**Decision reversed:** `auto` was the default for an hour; two existing network
tests failed because the stored text changed when trafilatura was installed. A
corpus must not change character because an optional package is present.

---

## Wave 12 — PDF tables · PR #8

Positioned text becomes a grid, or nothing. No PDF dependency on either side.

- `vrusha/store/pdf.py` — objects and `FlateDecode` via `zlib`, a content-stream
  tokeniser (`Tj TJ ' " Td TD Tm T* Tf`), rows from shared y **reversed** (a PDF's
  y axis points up), columns from recurring x, bands by vertical gap
- `.pdf` picked up by `extract`; the page text is stored for retrieval
- `examples/fixtures/tables/plans.pdf`, written by hand in the test helper

**Bugs:** every table came out upside down; a cell drawn as two runs more than
the column tolerance apart became two columns (now a stated limit).

---

## Wave 11 — JSON-LD · PR #7

Read the schema the page already publishes.

- `vrusha/store/jsonld.py` — grouped by `@type`, nested objects flattened
  (`offers.price` → `offers_price`), a single nested entity is a column not a
  table, several are a table, a broken block is skipped
- `x_tables.kind` (`jsonld` | `html-table`), added in place by a migration
- re-extraction is idempotent

**Bugs:** single nested entities produced one-row tables (`Brand`, `Offer`);
`extract` duplicated records on a second run; the single-file summary had a
different shape from the directory summary.

---

## Wave 10 — formulas · PR #6

Derived columns from a string that cannot execute anything.

- `vrusha/store/formula.py` — `ast`-walked whitelist: numbers, strings, columns,
  arithmetic, comparisons, `and/or/not`, `if/else`, and `min max round abs sum len
  if_null date_diff`. Attribute access, subscripts, comprehensions, lambdas and
  any other call are refused at compile time
- `vrusha derive --table N --name X --expr "..." [--guard ...]`, or `--spec`
- the whole spec compiles before any write

**Bug:** a function name is itself a `Name` node, so `round(price/3, 2)` was read
as a column called `round` and refused.

---

## Wave 9 — extraction · PR #4

Pages and CSVs become typed rows, and `verify --refetch`.

- `vrusha/store/extract.py` — HTML tables and CSV; header detection, type and
  unit inference; Devanagari/Arabic-Indic/Bengali digits, lakh grouping, scale
  words; `unit: mixed` rather than a sum that would be wrong
- `vrusha/store/records.py` — `x_tables` / `x_columns` / `x_rows`, rows as JSON,
  linked to document, chunk and licence
- `verify --refetch` — unchanged / drifted / rewritten / gone / licence-changed
  by word-shingle overlap, dry run unless `--apply`
- `vrusha extract --dir`, `vrusha tables`, `vrusha tables --id N`

**Bugs:** a single body row counted as "every row identical", rejecting every
two-row table; `%m/%d/%Y` sat in the day-first list.

---

## Wave 7 — benchmarks · PR #2

- `bench/suite.py` — recall@1/10/100 with distribution, p50/p95/p99, QPS, build
  time, index size, peak RSS, concurrency, pipeline stages
- `bench/report.py` → `docs/BENCHMARKS.md`, generated from JSON
- **The bug it found:** `is_stale()` called `vector_fingerprint()`, a `COUNT(*)`
  over 200k rows, **1,814 ms on every query**. Fixed with a revision counter in
  `meta`: native query latency 3,325 ms → 18.5 ms

---

## Wave 6 — dual-backend retrieval · PR #1

- `vrusha/store/ann.py` — segmented IVF over int8 or float32, exact rescore,
  atomic six-file format, staleness fingerprint
- `vrusha/store/backends.py`, `router.py` — exact | native | ragforge behind one
  `search()`; capability → resource → freshness → explore → speed → failover
- `vrusha/store/export.py`, `vrusha/serve.py` — SQLite → RagForge, and one JSON API
- `vrusha index build|stats|drop|export`, `vrusha backends`, `vrusha serve`,
  `vrusha query --explain`

**Bug:** the router only timed the backend that answered, so an unmeasured
backend could never win — it now probes it once.

---

## Waves 1–5 — before this file

`harvest` (search, fetch, robots, charset, gates), `address` (the numeric tree),
`passes` (merge, cluster, centroid, graph), `rules` (ten levels), `languages`
(20 profiles), `store` (SQLite + FTS5 + vectors), `tokenize`/`dataset`/`train`/
`generate` (BPE and TinyLM). The audit in `docs/audit/` records the defects found
in that period and their fixes.

---

## How to read a wave

Each wave is one PR, one squash commit on `main`, and one entry here. A wave that
finds a bug in an earlier wave gets its own entry (see the date fix), because the
fix is a change and the record should not quietly grow a correction.
