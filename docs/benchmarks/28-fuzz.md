# Fuzzing the parsers

**Benchmark:** `fuzz` · **measures:** rubbish through the parsers · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `payloads` | 40 |
| **`crashes`** | **0** |
| `ms` | 254.1 |
| `per_second` | 157.4 |

## What matters here

Throughput is unremarkable. **`crashes=0` is the number** — a parser that survives
fuzzing is one a caller can trust with a page it did not choose.

Each payload is deterministic rubbish (fixed seed) pushed through HTML extraction,
the safety scanner, `neutralise()` and the maths canonicaliser.

## What it does not measure

- PDF streams beyond the fixture
- adversarial sizes (payloads are 300 chars; a 500 MB page is untested)
- the OCR and browser seams — no binary to fuzz
