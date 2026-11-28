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

## Implemented so far

Steps 1 to 7 of the plan are in, with tests:

```bash
vrusha extract --dir ./pages --licence MIT    # html tables + csv -> typed rows
vrusha tables                                  # what was extracted, with schemas
vrusha tables --id 1                           # the rows themselves
vrusha derive --table 1 --name price_lakh \
       --expr "price / 100000" --guard "price > 0"
vrusha derive --table 1 --spec formulas.json   # several columns at once
vrusha verify --refetch                        # is what we stored still true?
vrusha verify --refetch --apply                # ... and write the result back
```

What it does, measured against the fixtures in `examples/fixtures/data/`:

| input | result |
|---|---|
| `plans.html`, a 5-column pricing table | 1 table, 3 rows, 80% typed columns |
| `₹1,23,456`, `Rs 5.5 lakh`, `₹2.4 crore` | `123456`, `550000`, `24000000` — with the raw text kept |
| `१५/१२/२०२६` | `2026-12-15` |
| `99.9%` | `percent`, unit `%` |
| `50 GB`, `500 GB`, `5 TB` | `int`, unit **`mixed`** — a sum would be wrong, so it is marked |
| a two-row nav table | rejected: *every row identical* / *no typed column* |
| `vendors.csv` | 1 table, `price:money`, `seats:int`, `renewal:date` |

Rows are stored as JSON in `x_rows` beside a schema in `x_columns`, so SQLite's
own JSON functions make them queryable:

```sql
SELECT SUM(json_extract(data,'$.price')) FROM x_rows WHERE table_id = 1;
```

`verify --refetch` re-fetches every `http` document and classifies it:
`unchanged` (shingle overlap >=95%), `drifted` (70-95%), `rewritten` (<70%),
`gone` (404/410, the code read back out of the error), `licence-changed`, or
`error`. It is a dry run unless `--apply` is passed, and `--apply` only writes
for `unchanged` (refresh `retrieved`) and `gone` (record the status).

## What to build next, in order

1. ~~`verify --refetch`~~ — done.
2. ~~Table extraction + schema inference for HTML/CSV~~ — done.
3. ~~Tier 1 formulas~~ — done (`vrusha/store/formula.py`, `vrusha derive`).
4. ~~JSON-LD and schema.org~~ — done (`vrusha/store/jsonld.py`).
5. ~~PDF tables~~ — done (`vrusha/store/pdf.py`, no PDF dependency).
6. ~~SearXNG + trafilatura, and `harvest --extract`~~ — done.
7. ~~Relation inference~~ — done (`vrusha/store/relations.py`, `vrusha joins`).
8. **Tier 2 sandboxed scripts** — last, because it is the only irreversible
   risk in the list, and a subprocess is not a sandbox.

## Formulas

A derived column is a string that arrives from a config file and runs against
values that came off a web page, so it is never handed to `eval`. It is parsed
with `ast`, walked against a whitelist, and interpreted by `formula.py`:

| allowed | not allowed |
|---|---|
| numbers, strings, `True`/`False`/`None` | attribute access (`x.__class__`) |
| column names | subscripts (`os.environ['X']`) |
| `+ - * / // % **`, unary `-`/`+` | comprehensions, lambdas |
| `== != < <= > >=`, `and`/`or`/`not` | any call not on the list |
| `a if b else c` | `import`, `eval`, `exec`, `open`, `__import__` |
| `min max round abs sum len if_null date_diff` | |

Refused at compile time, before a single row is read:

```
$ vrusha derive --table 1 --name x --expr "__import__('os').system('id')"
derive: only named functions may be called, not Attribute
$ vrusha derive --table 1 --name x --expr "price.__class__"
derive: Attribute is not allowed in a formula (price.__class__)
```

Semantics that matter for data rather than for tests: `None` propagates (a
missing price makes the derived value missing, not zero), division by zero is
`None` rather than an exception, and a guard (`"price > 0"`) makes a row
skippable without a `CASE` in every query. A spec is compiled in full before
any write, so a bad formula leaves the table exactly as it was.

```json
{"derived": [
  {"name": "price_lakh", "expr": "price / 100000", "type": "float"},
  {"name": "per_unit", "expr": "price / storage", "guards": ["storage > 0"]}
]}
```

## JSON-LD

Most product, event, recipe and organization pages already carry
`application/ld+json` with the schema spelled out. That is a better source than
any table on the same page: no header detection, no type guessing, and the page
tells you what each field means. `extract` reads it automatically, and
`vrusha tables` shows `kind: jsonld` beside `kind: html-table`.

| rule | why |
|---|---|
| entities are grouped by `@type` | ten products on a page are one table with ten rows, not ten tables |
| nested objects are flattened | `offers.price` becomes `offers_price`; a nested object is columns, not a JSON blob in a cell |
| a **single** nested entity is not a table | an `Offer` inside one `Product` is already `offers_price`; a one-row table is noise |
| several nested entities **are** a table | a page with five `Offer`s gets an `Offer` table with five rows |
| a broken block is skipped | sites ship one valid `Organization` and one invalid `BreadcrumbList` in the same page |
| `@context` and `@type` are not columns | the same value in every row is not data |
| arrays of scalars are joined | `recipeCuisine: [Indian, Gujarati]` -> `"Indian, Gujarati"` |

What the fixtures produce:

| page | tables | rows |
|---|---|---|
| `catalog.html` (`@graph`: 2 Products, 1 Organization) | `Product`, `Organization` | 2, 1 |
| `product.html` (1 Product, nested Offer/Brand/Rating) | `Product` | 1, with `offers_price`, `brand_name`, `aggregateRating_ratingValue` |

And the values arrive typed from a page that wrote them as strings:
`"₹4,999"` -> `4999`, `"Rs 5.5 lakh"` -> `550000`, `"२०२६-०९-१५"` -> `2026-09-15`.

Re-extracting a document replaces its records rather than doubling them, so
`extract` is safe to run again after a re-crawl.

## PDF

A PDF has no markup, so every table here is an **inference**. That is why this
module is two stages, and why the second one is allowed to refuse:

```
extract_text_items()   bytes -> (x, y, size, text) per run
tables_from_items()    items -> a grid, or a refusal with a reason
```

| step | how |
|---|---|
| objects and streams | regex over `obj ... endobj`, `zlib` for `/FlateDecode` |
| text and position | `Tj`, `TJ`, `'`, `"`, `Td`/`TD`/`Tm`, `T*`, `Tf`, with PDF string escapes |
| rows | shared y values, clustered to 3 points, **reversed** because a PDF's y axis points up |
| columns | recurring x positions, clustered to 12 points |
| a table, or not | the grid must be ≥60% full, ≥2 rows, ≥2 columns |
| prose on the same page | split into bands by vertical gaps, so a paragraph never becomes rows |

On the fixture: 13 text runs become one table of 4 rows (header + 3), with
`price: money` and `uptime: percent` inferred, and the paragraph below it stays
in the document text where retrieval can find it.

What it will not read, and returns nothing rather than nonsense for: encryption,
Type0/CID fonts with custom encodings, text drawn as vector outlines, rotated
pages. A table of mojibake is worse than no table.

## Search, fetch, extract

The loop is one command now:

```bash
export SEARXNG_URL=http://127.0.0.1:8080      # your own instance, no API key
vrusha harvest --query "hosting plans" --backend searxng --limit 5 \
               --licence MIT --extract
```

`--extract` reads the **markup** the fetcher already has in hand, rather than
re-inferring a table from flattened text. One page can yield several things at
once: the `<table>` on it, the `application/ld+json` beside it, and the prose
that is neither, which still becomes chunks for retrieval.

| piece | state before | state now |
|---|---|---|
| search | five backends, `searxng` already among them | unchanged; a stub instance is in the tests |
| fetch | gated, charset-detected, robots-respecting | keeps `Document.html`, so extraction needs no second fetch |
| text extraction | tag stripper only | `harvest.extractor`: `simple` (default), `trafilatura`, `auto` |
| tables and entities | `extract --dir` on files | `harvest --extract` on fetched pages |

On the boilerplate: the tag stripper already drops `nav` and `footer` by tag,
so trafilatura's real gain is the boilerplate **inside** the body — a cookie
banner in a `div`, a sidebar, a "related articles" block. It only removes what
it can tell apart from content, so on a thin page it keeps everything, which is
the safe direction. That is why the default stays `simple`: a corpus should not
change character because an optional package happens to be installed.

## Relations between tables

Six tables from six pages are six piles of rows until something says `sku` in
one is `sku` in another. `vrusha joins --build` finds those, and stores the
evidence rather than a claim:

```bash
vrusha joins --build      # infer and store
vrusha joins              # what it found, with the numbers
vrusha joins --table 3    # one table's relations
```

```
  left  column  right  column  shared  overlap  kind
  3     plan    4      plan    3       100%     same-column
  3     plan    6      plan    3       100%     same-column
```

| rule | why |
|---|---|
| a key must be unique, non-null and short | a column with duplicates is a category, not an identifier |
| **a mostly-numeric column is a measure** | two tables sharing the value `123456` share a number, not a thing — the first run joined `price` and `uptime` and was wrong |
| unless the name says identity | `sku`, `id`, `code`, `name` are keys even when numeric |
| overlap is Jaccard, `shared` is the count | `overlap: 1.0, shared: 3` is a fact; `overlap: 0.4, shared: 2` is a coincidence to look at |
| each pair is stored once, in a stable order | a re-run produces the same rows |
| nothing joins automatically | the edges are for a human to accept before a query relies on them |

What it does not do: entity resolution (two spellings of one vendor), transitive
closure across a chain of joins, or fuzzy matching. Each can be wrong in a way
that silently merges two companies into one.

## Known limits, stated

- **Units are not converted.** `50 GB` and `5 TB` are both stored as integers
  with `unit: mixed`; the conversion is the query's job, not the parser's. A
  silent conversion would be a wrong number that looks right.
- **A table split across two chunks** matches neither, so `chunk_id` is null and
  the row keeps its document and span instead. Provenance survives; the chunk
  link does not.
- **CSV has no type metadata**, so a column of bare digits is `int` even if it
  is a postal code. The `__raw` field keeps what was written.
- **The licence check only fires when both sides have one.** A page that stopped
  declaring a licence does not overwrite a stored SPDX id — a missing signal is
  not a changed signal.
- **`--refetch` is one request per document**, rate-limited per host. At 10k
  documents that is a slow job, not something you run on every commit.
