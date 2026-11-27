# From raw pages to related records

Today the store holds two things: **documents** (url, title, licence, text) and
**chunks** (a slice of text with an address and a vector). That is the right
shape for "find the paragraph that answers this". It is the wrong shape for
"which five vendors raised prices in Q3", because the answer lives in a column
of a table on one of those pages, and a chunk cannot be grouped or summed.

This document is the design for the missing layer: raw page in, **typed rows
with relations** out, every row still traceable to the chunk and the URL it came
from.

## The gap, stated plainly

| question | today | after |
|---|---|---|
| "what does the licence say" | retrieve the chunk, read it | same |
| "list every plan and its price" | retrieve 40 chunks, hope | `SELECT * FROM plan ORDER BY price` |
| "average price by vendor" | not possible | one `GROUP BY` |
| "which pages disagree about the price" | not possible | join rows to chunks to documents |
| "is this row still true" | not possible | re-fetch the source, compare |

Retrieval answers questions about *text*. A relational layer answers questions
about *facts*, and the two need to share one provenance chain: every row knows
the chunk it came from, the chunk knows the document, the document knows the URL
and the licence. Nothing is extracted without that chain intact.

## The layer

```
fetch ──► extract ──► infer schema ──► validate ──► store ──► link
 (have)     (new)        (new)          (new)        (new)    (new)

  HTML/CSV/JSON/PDF
        │
        ├─ HTML <table>          ─► rows + header detection   [tablextract, DuckDB webbed]
        ├─ JSON-LD / schema.org  ─► typed entities            [ETK]
        ├─ definition lists      ─► key/value records
        ├─ CSV / Excel           ─► typed columns
        └─ prose                 ─► chunks (what the store does now)
```

Three rules make this safe to add to a system that already works:

1. **Extraction never invents a row.** Every cell carries the source chunk id,
   the character offset inside it, and the extraction method. A row that cannot
   point at its own text is dropped, not stored.
2. **A table is a schema plus rows, not a blob.** Column names, inferred types
   (`int`, `float`, `money`, `date`, `percent`, `text`, `enum`), units and the
   null rate are all stored, so a later query knows what it is reading.
3. **Relations are inferred, then confirmed.** Two tables that both name a
   vendor are joined on the normalised vendor key, and the join is stored as an
   `edge` with a confidence, exactly like the address graph stores neighbours.

## Schema inference

For each extracted table:

| step | method | failure it prevents |
|---|---|---|
| header detection | first row vs body: type mismatch, casing, length distribution, `<th>` | treating a data row as a column name |
| type inference | try `int`, `float`, then `money`/`date`/`percent` per the language profile; accept when ≥90% of non-null cells parse | prices stored as strings, sums silently wrong |
| unit capture | `₹`, `Rs`, `INR`, `$`, `lakh`, `crore`, `Cr`, `k`, `M` | adding 5 lakh to 5 crore |
| key detection | a column that is unique and non-null becomes the primary key; else a synthetic row id | duplicate rows on re-crawl |
| relation inference | repeated entity values across tables → foreign key candidate, scored by overlap | two disconnected islands of tables |
| junk filter | <3 rows, >50% empty, a single column, or numeric-only single row | nav bars and layout tables |

## Language

The store already ships profiles for `en`, `hi`, `mr`, `ta`, `bn` plus 15 code
and data formats (`vrusha/languages/profiles/`). The extraction layer uses them
for four things, and this is where a multilingual pipeline usually fails:

| concern | what is done |
|---|---|
| header language | the profile's stopwords and common column names, so `कीमत` and `price` land in the same column name |
| digits | Devanagari `०-९`, Arabic-Indic `٠-٩`, Tamil `௦-௯` normalised to ASCII before parsing a number |
| grouping | `1,23,456` (Indian lakh grouping) and `1,234,567` both parse; the separator is decided by the digit pattern, not the locale |
| scale words | `लाख`/`lakh`/`L`, `करोड़`/`crore`/`Cr`, `हज़ार`/`thousand`/`k` become a multiplier, never a suffix |
| dates | `27/11/2026`, `2026-11-27`, `२७/११/२०२६`, `27 Nov 2026` all resolve; ambiguity is resolved by the profile, and an unresolved date is stored as text with `date_confidence: low` |
| script mixing | a cell with two scripts keeps both; the row records which profile matched and at what confidence |

Nothing here needs a model. That matters: it runs on a CPU box, deterministically,
and a wrong parse is a bug you can fix rather than a sample you have to
re-roll.

## Web search

`vrusha search` already exists with backends `fixtures | ddg | searxng | tavily |
brave`, and `harvest` already gates, chunks and stores. Two things are missing
compared with what the open-source projects do well:

| missing | the fix | why |
|---|---|---|
| a keyless, self-hosted search | **SearXNG** — already a configured backend, needs the container | no API key, no per-query cost, no vendor rate limit |
| clean extraction from arbitrary HTML | **trafilatura** (optional dependency) instead of the current tag-stripper | boilerplate removal decides corpus quality more than chunking does |
| a fetch-then-verify loop | see below | a crawl is a hypothesis; verification is the test |

Reference implementations worth reading before writing more: `yilongchua/websearch`
(SearXNG + Crawl4AI behind one JSON API), `siftdog` (Tavily-compatible, SSRF
protected), and `blakemartz/private-search-4-agents` (a fetch cascade that
degrades gracefully). All three converge on the same shape: **search for
candidates, fetch the top N, extract clean text, cite the URL**.

## Running scripts and formulas

This is the part to be careful about, so it is split into two tiers and only
one of them is on by default.

**Tier 1 — derived columns (safe, default).** A formula is parsed with
`ast.parse`, walked, and evaluated against a whitelist: numbers, strings,
column names, `+ - * / // % **`, comparisons, `and/or/not`, and a fixed function
list (`min`, `max`, `round`, `abs`, `sum`, `len`, `if_null`, `date_diff`).
No attribute access, no calls to anything not on the list, no imports, no I/O,
no `eval` of a string that came from a page. A formula that fails the walk is
rejected with the node that broke it.

```json
{"derived": [
  {"name": "price_per_sqft", "expr": "price / area", "type": "float",
   "guards": ["area > 0"]}
]}
```

**Tier 2 — user scripts (opt-in, sandboxed).** Running arbitrary Python against
scraped data is a different risk class: the input is untrusted, and so is the
script. It runs in a **separate process** with no network, a read-only view of
its input, an rlimit on CPU and memory, and a wall-clock timeout. Never in the
serving process, never with the store open. Off unless `--allow-scripts` is
passed, and the report says which rows were produced by a script.

The honest note: tier 2 is a remote-code-execution surface by design. If the
corpus is untrusted and the scripts are untrusted, the only real boundary is the
OS — a container, not a subprocess. Say so in the docs rather than implying a
subprocess is a sandbox.

## Verifying stored data against the web

`vrusha verify` already checks the store's internal consistency (dims, orphans,
counts). The question here is different: **is what I stored still true?**

A `--refetch` mode, one row at a time:

| check | signal | action |
|---|---|---|
| URL still resolves | HTTP status, redirect chain | 404/410 → mark document `gone`, keep the chunks, stop citing them |
| licence unchanged | re-run `detect_licence` on the fetched page | changed or removed → `filter.policy` re-runs, the document can be deactivated |
| text drifted | shingle hash of the stored text vs the fetched text | <5% changed: refresh `retrieved`; >30%: mark `stale`, re-chunk and re-embed |
| row still supported | the extracted row's source span still contains its cells | the row is invalidated, not silently kept |
| extraction was right | a sample of rows re-parsed from the fresh HTML | disagreement rate per site, so a broken extractor is visible |

The output is a drift report per domain, not a boolean: *"3 of 40 rows on
example.com no longer match; the price column changed on 2 pages; the licence
changed on 1."* That is what makes a corpus trustworthy rather than merely
large.

## What to build, in order

1. **`vrusha verify --refetch`** — smallest, highest value, uses code that
   already exists, and answers "is my stored data proper". No new dependency.
2. **Table extraction + schema inference** for HTML/CSV into `rows`/`columns`
   tables, linked to chunks. Deterministic, no model, testable against fixtures.
3. **Tier 1 formulas** — derived columns, AST-restricted.
4. **SearXNG + trafilatura** as the search/extract path, behind the existing
   `harvest` command.
5. **Relation inference** across tables (entity keys, join edges, confidence).
6. **Tier 2 sandboxed scripts** — last, because it is the only irreversible
   risk in the list.

Each step has an acceptance test that fails today: a fixture page whose table
must become 5 typed rows with the right units, a drifted URL that must be
flagged, a formula that must be refused for importing `os`.
