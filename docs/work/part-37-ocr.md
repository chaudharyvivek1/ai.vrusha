# Part 37 — images: metadata measured, text a seam

**Wave 61 · `vrusha/store/ocr.py`, `bench/registry.py`**

## Two things, only one needs a binary

| | needs | status |
|---|---|---|
| **what an image is** — format, dimensions, mode | Pillow (already a dependency) | **measured** |
| **what an image says** — OCR text | Tesseract, a system binary | **seam only** |

```
describe(png) → {'bytes': 173, 'format': 'png', 'width': 120, 'height': 40, 'mode': 'RGB'}
maybe_read(null, png) → ('', False, 'image (png, 173 bytes) and no ocr engine is configured')
```

Recording that a page carried a 1200×800 PNG is useful on its own, and it needs no
OCR at all — that half of "document intelligence" now exists.

## The benchmark

```
detect_us=1.09  describe_ms=0.07  bytes=2787
```

**1.09 µs** to identify an image by magic bytes, **0.07 ms** to read its dimensions
from a 2.8 KB PNG. Both are free next to a 9 ms retrieval.

## Why magic bytes and not the extension

An extension is a claim; magic bytes are a fact. A `.png` that is HTML gets
refused, which is the correct answer and the one that stops a parser being fed the
wrong thing.

## The honest limit

**Tesseract is not installed and cannot be** — no root on this machine, no
`tesseract` binary. So:

| what | status |
|---|---|
| detection | **measured** — 1.09 µs |
| metadata | **measured** — 0.07 ms |
| the seam | **tested** — 14 tests, mostly refusals |
| Tesseract itself | **unmeasured** |
| default | **`none`** |

The same distinction as Parts 16 and 36: built, tested, **not adopted on evidence
that does not exist**.

## Errors found and fixed

**None.** `describe()` was written to never raise — a broken image is data, not an
exception — and a test feeds it a PNG header followed by garbage to prove it.

## Found while rechecking Parts 1–36

Nothing. `ruff` clean, 779 tests green.

## What is next

**Part 38: the external datasets** deferred since Phase 0 — BEIR/CRAG subsets, as
datasets rather than leaderboards. That is the last Phase 7 part, and the only one
that can put a number on retrieval quality against someone else's labels.

## Checklist for this part

- [x] magic-byte detection, not extensions (tested)
- [x] dimensions and mode, never raising (tested, including garbage)
- [x] the default declines with the image's own description in the reason (tested)
- [x] a non-image never reaches an engine (tested: `calls == 0`)
- [x] an engine that reads nothing, or raises, degrades with a reason (tested)
- [x] an unknown backend refused with the list (tested)
- [x] benchmarked: 1.09 µs detect, 0.07 ms describe
- [x] **Tesseract unmeasured, and the work log says so**
- [ ] external datasets (Part 38)
