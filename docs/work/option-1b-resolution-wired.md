# Option 1b — resolution wired into the graph

**Wave 66 · `vrusha/store/entities.py`, `vrusha/store/edges.py`, `bench/registry.py`**

## What was asked, and what happened

Wire `entities.resolve` into the graph so `Technologies` and `Alpha Design
Technologies Pvt Ltd` stop being two nodes.

**Result: a bug in the resolver, a small merge, and a negative finding that is more
useful than the fix.**

| | before | after |
|---|---|---|
| nodes | 159 | **157** |
| communities | 45 | **42** |
| edges | 124 | 124 |
| names merged | 0 | **42** |

Two nodes and three communities. Real, and small — because most of the duplicates
this corpus has are **not** resolution failures.

## The bug the wiring exposed

`canonical_map` keyed its result by the **normalised** form and returned `{}` when
two spellings normalised identically:

```python
canonical_map(["Acme Corp", "ACME Corporation"])   # -> {}   ← wrong
```

Both become `acme corporation` after abbreviation expansion, so the group had one
member and the function bailed — **the one case it exists for produced no mapping at
all.** Fixed: the mapping is keyed by the original spelling, because that is what a
caller holds.

## The negative finding

```
canonical_map(["Alpha Design Technologies Pvt Ltd", "Technologies"])  ->  {}
_ratio("alpha design technologies pvt ltd", "technologies")           ->  45.3
threshold                                                             ->  90.0
```

**The resolver refuses, and refusing is correct.** `Technologies` could belong to any
of a dozen companies in this corpus; merging a bare suffix into one of them would be
a false merge, and a false merge is worse than a duplicate because it is invisible.

So the duplicate is an **extraction** artefact, not a resolution failure: the run
`Adani Defence Systems and Technologies, a wholly owned subsidiary` stops at the
five-word cap, and a later bare `Technologies` becomes its own entity. The fix
belongs in `prose.py`'s run boundaries, not here.

That is worth writing down: **wiring the resolver in did not solve the visible
problem, and the reason it did not is the interesting part.**

## Errors found and fixed

**1. `canonical_map` returned `{}` for the case it was built for** — described above.
**2. My own two test expectations were wrong** — written against the old
(normalised-key) contract. Sixth time in this programme that the failing line was
the test.

## What it does not do

| limitation | why |
|---|---|
| merge a suffix into a company | 45.3 < 90, deliberately |
| merge across languages | the ratio is character-based, no transliteration |
| fix the extraction that produced the bare name | that is `prose.py`, a separate change |

## Found while rechecking

Nothing else. `ruff` clean, 869 tests green.

## What is next

**Option 2: re-run the graph boost** on this corpus — the first honest test of
whether it helps, now that there are 124 edges and 42 communities to boost with.

## Checklist

- [x] resolution wired into `edges.extract` (tested)
- [x] `canonical_map` keyed by the original spelling (bug fixed, tested)
- [x] a bare suffix is **not** merged (tested, with the ratio in the docstring)
- [x] measured: 159 → 157 nodes, 45 → 42 communities, 42 names merged
- [x] the negative finding written down rather than papered over
- [ ] the extraction fix that would actually remove the bare name
