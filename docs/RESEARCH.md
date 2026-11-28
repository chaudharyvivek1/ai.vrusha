# Research: a question, an answer, and every source that made it

```bash
vrusha research "what is the Euler identity" --verify --licence MIT
```

```
Q: what is the Euler identity

   Euler's identity is e^{i\pi}+1=0, which follows from the series expansion.
   ...

Sources:
  [1] http://example.org/euler
      Euler and the quadratic
      licence MIT, 3 chunk(s), 2 table(s), 2 formula(s), verified: unchanged
```

The command composes what already existed — `search`, `harvest`, `extract`,
`ask`, `verify --refetch` — and returns the **sources as a first-class output**.

## What a source is

Not a URL. A URL, a title, a licence, when it was retrieved, how many chunks and
formulae came out of it, and whether re-fetching it today still returns the same
text. That last field is what makes an answer auditable rather than merely cited.

| field | why it is there |
|---|---|
| `url`, `title` | where to look |
| `licence` | a formula without a licence is a rumour with a URL |
| `chunks`, `tables`, `equations` | what this page actually contributed |
| `verified` | `unchanged` / `drifted` / `rewritten` / `gone`, by word-shingle overlap against a fresh fetch |
| `rejected` | the gates refused to store it, and this says why |

## The gates are not relaxed

A page that is too short, or whose licence is unknown, is refused during a
research run for exactly the same reason it is refused during a crawl — and the
refusal appears in the source list rather than the page disappearing from it.

```
  [1] http://example.org/short
      NOT STORED: licence: no licence recorded; unknown is a refusal; size: 87 words, minimum 120
```

`--licence MIT` asserts a licence for the pages you fetched. That is an explicit
act by the caller, which is why it is a flag and not a default. The size
threshold lives in `vrusha/rules/levels/level-06/01-thresholds.json`.

An earlier version of this module quietly lowered the size gate for research by
setting `cfg["harvest"]["min_words"]`. It had no effect: the gate reads
`rules["quality.min_words"]`, not the config. The tests caught it because the
refusal reason still said "minimum 120" — a reminder that a config key which is
read by nothing is worse than no key at all.
