# Part 36 — JavaScript-rendered pages

**Wave 60 · `vrusha/harvest/render.py`, `bench/registry.py`**

## What was built

A seam, and a refusal to pay for it by default:

- **`NullRenderer`** is the default and always declines, so `pip install vrusha`
  stays small and a machine without Chromium behaves exactly as before
- **`PlaywrightRenderer`** is optional, imported only when chosen; its absence is a
  clear sentence, not a traceback
- **detection is free** — a ratio of visible text to markup, no browser started to
  decide whether to start a browser

```
shell? True | real page? False
maybe_render → (unchanged html, False, "page looks like a shell (0 characters
                of text) and no renderer is configured")
```

## The benchmark

```
shell_us=26.3  real_us=115.8  text_us=26.1  shell_bytes=977
```

Detection costs **26 µs on a shell and 116 µs on a real page** — a rounding error
next to a 9 ms retrieval, which is the property that matters: a check that costs a
millisecond would be a tax on every fetch to save a few.

## The honest limit, and it is the important part

**The browser is not installed here, so its cost and its success rate are unknown.**
This part is a seam, not an adoption — the same distinction as the cross-encoder in
Part 16, which was built, measured and **not** adopted.

| what | status |
|---|---|
| detection | **measured** — 26–116 µs |
| the seam | **tested** — 13 tests with a fake renderer |
| Playwright itself | **unmeasured** — not installed |
| default | **`none`** |

That is why `harvest.render.backend` defaults to `none`: turning it on would be a
300 MB decision made on no evidence.

## What the ratio cannot see

| case | what happens |
|---|---|
| a page that looks static and still needs JS | not rendered — the ratio says it has text |
| a page that is genuinely empty | reported as a shell, and a renderer would be called for nothing |
| a shell under 200 bytes | not judged at all; `MIN_MARKUP` declines |

Each of those is a rule with a threshold, and thresholds are guesses until measured
on real pages — which needs the browser this machine does not have.

## Errors found and fixed

**None.** The detector was checked on both shapes before any test was written, and
the tests are mostly refusals.

## Found while rechecking Parts 1–35

Nothing. `ruff` clean, 766 tests green.

## What is next

**Part 37: multimodal** — OCR and image text, the other half of "document
intelligence" that the review rated 3/10.

## Checklist for this part

- [x] a shell is detected, a real page is not (tested)
- [x] detection is free — 26 µs measured
- [x] the default declines and says why (tested)
- [x] a shell without a renderer says so in a sentence (tested)
- [x] a real page never starts a browser (tested: `calls == 0`)
- [x] a renderer that returns nothing, or raises, degrades with a reason (tested)
- [x] an unknown backend is refused with the list (tested)
- [x] **the browser is unmeasured, and the work log says so**
- [ ] OCR / image text (Part 37)
