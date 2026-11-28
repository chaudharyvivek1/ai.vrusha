# Part 23 — relations with evidence

**Wave 48 · `vrusha/store/edges.py`, `bench/registry.py`**

## What was built

Nine business predicates — `acquired`, `supplies`, `subsidiary_of`,
`partnered_with`, `appointed`, `located_in`, `reported`, `competes_with`,
`works_for` — each producing an edge with **the sentence that asserted it**.

```python
>>> [(e.subject, e.predicate, e.object) for e in extract(text)]
[('Acme Corporation', 'acquired', 'Beta Limited'),
 ('Beta Limited', 'supplies', 'Gamma Industries'),
 ('Meera Rao', 'works_for', 'Reserve Bank of India')]
```

Stored in `x_edges` with subject, predicate, object, **sentence**, confidence and
doc/chunk id; `neighbours(name)` returns one hop in both directions, evidence
included.

## The benchmark, and the zero in it

```
edges=0  with_evidence=0  chars_per_second=53945
predicates=0  fixture_edges=5  fixture_evidence=5
```

**Zero edges on the documentation corpus, and that is the finding.** The patterns
are business relations; `docs/` is technical prose about a retrieval engine. There
is no `acquired` in it.

The fixture corpus reports **5 edges, 5 with evidence** — so the extractor works,
and the corpus is what has no relations in it. Reporting the zero rather than
hiding it is the same rule as the `model2vec=0` flag in the retrieval benchmark: a
number needs its context or it is a number about the wrong thing.

## Errors found and fixed

**1. `Acme acquired March`.** A month became the object of an acquisition, because
`in March` matches the place-preposition rule and so carried confidence 0.7 — above
the threshold I had just added. Fixed with a calendar stoplist, and the reason is
written next to it: a relation cannot hang off a date.

**2. My own test was wrong.** `test_a_verb_across_two_sentences_makes_no_edge`
asserted *no* edges from a two-sentence text — but the second sentence has a real
edge in it. The test now asserts the right thing: no **cross-sentence** edge, and
the intra-sentence one is present.

## What it will not do

| limitation | why |
|---|---|
| a verb outside the nine | no edge — a missing edge is a gap, an invented one is a wrong answer |
| relations across sentences | not attempted; a pronoun subject breaks them |
| passive voice other than the listed forms | missed |
| three-way relations | binary only |

## Found while rechecking Parts 1–22

Nothing. `ruff` clean, 647 tests green, benchmark coverage accepted the new module.

## What is next

**Part 24: community detection.** Entities and edges now exist; the next part finds
the clusters — label propagation over the edge graph, no dependency — which is the
piece the outside review specifically named as missing ("nearest-neighbour edges
are not a knowledge graph").

## Checklist for this part

- [x] nine predicates, each with a pattern and a confidence
- [x] every edge carries its sentence (tested: verbatim)
- [x] a month cannot be an argument (tested)
- [x] no verb, no edge (tested)
- [x] edges round-trip through the store, and a re-extract replaces rather than duplicates
- [x] `neighbours()` in both directions
- [x] benchmarked, including the zero and why it is zero
- [ ] communities over the graph (Part 24)
