# BEIR sets

**Benchmark:** `beir` · **measures:** reading a BEIR set and scoring one query · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `read_ms` | 1.90 |
| `score_us` | 11.7 |
| `documents` | 5 |
| `queries` | 5 |

## What each number is

- `read_ms`: parse corpus.jsonl, queries.jsonl and qrels/test.tsv — **1.9 ms** for the fixture
- `score_us`: recall, precision, MRR and nDCG for one query against its judgements — **11.7 µs**

The loader is not where time goes; retrieval is.

## What is NOT measured

**A real BEIR dataset.** `huggingface.co` answers 200, but the dataset file endpoints
return 404 from this sandbox, and the official BEIR host refuses the request. The
loader is tested against a bundled five-document fixture instead.

The format is the part that has to be right — a wrong loader silently scores the
wrong thing. The bytes are a download away.
