# Entities from prose

**Benchmark:** `entities` · **measures:** entities from prose, over the whole corpus · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `entities` | 1,394.00 |
| `chars_per_second` | 63,855.00 |
| `doc_ms` | 2,919.30 |
| `kinds` | 4.00 |

## What each number is

- `entities`: entities found across every `docs/*.md`
- `chars_per_second`: throughput — the number that means something
- `doc_ms`: one document; measured on the largest file, so it is a worst case
- `kinds`: distinct kinds seen (organisation, person, place, proper)

## Method

Rule-based extraction, no model. In-process, this pod. Harness:
`bench/registry.py::bench_entities`.

## What this does not measure

Precision and recall against a labelled corpus — there is no gold set for prose
entities yet, so the counts are *what the rules found*, not *what is there*. That
is the honest limit, and it is why the module docstring lists what the rules miss.
