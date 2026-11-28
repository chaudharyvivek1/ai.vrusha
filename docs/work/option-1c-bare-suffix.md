# Option 1c — a bare corporate suffix is not a name

**Wave 68 · `vrusha/store/prose.py`, `tests/test_prose.py`**

## The bug

```
"Technologies"  ->  organisation, confidence 0.9
"Limited"       ->  organisation, confidence 0.9
"Services"      ->  organisation, confidence 0.9
```

The classifier gave **0.9 confidence** to a single word whenever it appeared in
`ORG_SUFFIXES` — the list meant to recognise `... Pvt Ltd` at the *end* of a name.
A sentence that begins with `Technologies` therefore produced a high-confidence
organisation, and the graph got a node that is not a company.

Found on the real corpus, where `Technologies` appeared as a **subject** beside
`Alpha Design Technologies Pvt Ltd` — the duplicate that wave 66's resolver
correctly refused to merge, because 45.3 is below the 90 threshold.

**Two symptoms, one cause.** The resolver was right to refuse; the extractor was
wrong to emit the fragment.

## The fix

A run of one word that is a bare suffix is skipped:

```python
if len(run) == 1 and run_lower[0] in ORG_SUFFIXES:
    continue
```

## The measurement

| | before | after |
|---|---|---|
| bare suffix entities | 8 | **0** |
| nodes | 157 | **155** |
| edges | 124 | **121** |
| communities | 42 | 42 |

The three lost edges were all attached to fake nodes. **A graph that loses edges
when a fake node disappears is a graph getting more correct.**

## The bug this exposed, and did not fix

```
Andhra Prade Bharat Petroleum Corporation
```

A run crossed a sentence boundary: `Andhra Pradesh` truncated mid-word by the source
text, then the next sentence's `Bharat Petroleum Corporation` joined to it. The
`_sentence_start` check looks for punctuation, and a truncated word has none.

Recorded, not fixed: it needs the sentence splitter to notice a run that jumps a
capital-letter sequence across a line, which is a different change with its own
risk.

## Errors found and fixed

**One** — the bare-suffix classification above.

## Found while rechecking

Nothing. `ruff` clean, 877 tests green.

## What is next

**Option 2: a denser graph.** At 121 edges over 155 nodes a community has at most 8
members, and wave 67 showed the boost cannot reach the ranking at that density. More
articles is the only way to test whether it ever could.

## Checklist

- [x] a bare suffix is no longer an entity (tested, five words)
- [x] a real organisation still is (tested)
- [x] the suffix rule still fires inside a name (tested)
- [x] measured: 8 → 0 fragments, 157 → 155 nodes, 124 → 121 edges
- [x] the next bug found and recorded rather than quietly fixed
