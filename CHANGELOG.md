# Changelog

Every change that landed on `main`, in the order it landed, with what it did and
what it cost. Waves 1–5 predate this file; they are the harvest, address, passes,
gate and model layers the rest builds on.

Format: **what changed** · **new files** · **the bug the tests or the demo found**.

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
