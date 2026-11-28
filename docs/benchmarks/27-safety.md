# Scanning for instructions

**Benchmark:** `safety` · **measures:** scanning a page for instructions · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `benign_ms` | 0.113 |
| `hostile_ms` | 5.151 |
| `neutralise_ms` | 4.628 |
| `kb` | 11.3 |

## The improvement this benchmark forced

The first version scanned 11 KB in **4.76 ms** — a **50% tax on a 9 ms query**, on
every page. Unacceptable, so a keyword gate went in: every pattern needs one of 24
words, and a page with none of them never runs a regex.

| | before | after |
|---|---|---|
| benign | 4.76 ms | **0.113 ms** — 42× faster |
| hostile | 5.11 ms | 5.15 ms — unchanged, it must run |

## What each number is

- `benign_ms`: an ordinary page — the common case, and the one that had to be cheap
- `hostile_ms`: a page with an injection — the regexes run, and it costs what it costs
- `neutralise_ms`: the same scan plus span replacement

## What this does not measure

Whether it catches *real* attacks. The patterns are English and narrow; a translated
or plainly-worded injection slips past. This is a floor, not a guarantee.
