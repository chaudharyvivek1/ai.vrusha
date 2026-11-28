# Relations with evidence

**Benchmark:** `edges` · **measures:** relations with evidence, over the whole corpus · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `edges` | 0 |
| `with_evidence` | 0 |
| `chars_per_second` | 53,945 |
| `predicates` | 0 |
| `fixture_edges` | 5 |
| `fixture_evidence` | 5 |

## The zero, explained

**The documentation corpus produces no edges, and that is the finding.** The
patterns are business relations — `acquired`, `supplies`, `subsidiary_of` — and
`docs/` is technical prose about a retrieval engine. There is no acquisition in it.

The fixture corpus (five sentences of business news) reports **5 edges, all 5 with
evidence**. So the extractor works; the corpus is what has no relations in it.

Reporting the zero rather than hiding it follows the same rule as the
`model2vec=0` flag in the retrieval report: a number needs its context, or it is a
number about the wrong thing.

## Method

Pattern-based extraction, no model. In-process, this pod. Harness:
`bench/registry.py::bench_edges`.

## What this does not measure

Precision and recall against a labelled relation set — there is no gold set yet.
The counts are *what the patterns found*.
