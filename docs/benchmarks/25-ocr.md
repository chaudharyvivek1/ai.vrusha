# Identifying an image

**Benchmark:** `ocr` · **measures:** identifying an image, which needs no binary · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `detect_us` | 1.09 |
| `describe_ms` | 0.07 |
| `bytes` | 2,787 |

## What each number is

- `detect_us`: magic-byte check on a 2.8 KB PNG — **1.09 µs**
- `describe_ms`: open it and read width, height, mode — **0.07 ms**

Both are free next to a 9 ms retrieval.

## What is NOT measured

**Tesseract.** Not installed, and cannot be: no root, no `tesseract` binary on this
machine. Its accuracy and speed are unknown, so the OCR engine defaults to `none`
and the seam is tested with a fake engine only.

## Why magic bytes, not extensions

An extension is a claim; magic bytes are a fact. A `.png` that is HTML is refused,
which stops a parser being fed the wrong thing.
