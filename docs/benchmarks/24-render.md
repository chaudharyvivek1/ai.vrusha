# Shell detection

**Benchmark:** `render` · **measures:** shell detection, which must be free · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `shell_us` | 26.3 |
| `real_us` | 115.8 |
| `text_us` | 26.1 |
| `shell_bytes` | 977 |

## What each number is

- `shell_us`: deciding a 977-byte SPA shell is a shell — **26 µs**
- `real_us`: the same decision on a page with real text — 116 µs (more text to strip)
- next to a **9 ms** retrieval, both are a rounding error

## Why free matters

A detection step that costs a millisecond would be a tax on every fetch to save a
few. At 26 µs it can run on every page without being noticed.

## What is NOT measured

**Playwright itself.** The browser is not installed on this machine, so its cost and
its success rate are unknown. This part is a **seam, not an adoption** — the default
is `none`, because turning it on would be a 300 MB decision made on no evidence.
