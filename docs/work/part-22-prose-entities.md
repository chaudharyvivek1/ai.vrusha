# Part 22 — entities from prose

**Wave 47 · `vrusha/store/prose.py`, `bench/registry.py`**

## What was built

A rule-based entity extractor: organisations by suffix, people by honorific,
places by preposition, and a fallback for any run of capitalised words.

```python
>>> extract("Acme Corporation signed the deal in Pune. Dr. Meera Rao of the
...         Reserve Bank of India attended.")
organisation  0.9  Acme Corporation
place         0.7  Pune
person       0.85  Meera Rao
organisation  0.9  Reserve Bank of India
```

**Why rules and not a model:** a small NER model is 100–400 MB and another
dependency; this project's core stays numpy-only. The rules are the ones a careful
reader uses, and they are wrong in ways that are written into the module docstring
rather than hidden.

## The benchmark

```
entities=1394  chars_per_second=63855  doc_ms=2919.3  kinds=4
```

**63,855 characters per second** over the whole documentation corpus, 1,394
entities found. The per-document figure looks slow (2.9 s) because it is measured
on `README.md`, the largest file in the corpus — the throughput number is the one
that means something.

## Errors found and fixed — three, all by running it

**1. A run crossed a sentence break.** `Pune. Dr Meera Rao` came out as one
entity. The period sat *inside* the previous token, so the whitespace check could
not see the break; the fix is to test `_sentence_start()` at the next token's
position.

**2. `Reserve Bank of India` split at `of`.** Fixed with a connector list
(`of the and & de van…`), allowed inside a run but never at the end of one.

**3. `The Reserve Bank of India`** — the leading article was part of the name.
Articles are now dropped from the front of a run.

Each was found in the first thirty seconds of using the extractor on a real
sentence, and none would have been found by reading the code.

## What it will not do — stated, not hidden

| limitation | why it matters |
|---|---|
| an organisation with no suffix and no capital (`acme`) | missed entirely |
| `Jordan` the person vs `Jordan` the country | no disambiguation |
| a name only ever written in lowercase | invisible |
| coreference (`she`, `the company`) | not attempted |

Those are model jobs. Recording them is what makes the rule-based version usable:
a caller knows what to trust.

## Found while rechecking Parts 1–21

Nothing. `ruff` clean, 634 tests green, the benchmark coverage test accepted the
new module because it ships with a benchmark of its own.

## What is next

**Part 23: relation extraction with evidence.** Entities alone are a list. An edge
— `Acme Corporation` *supplies* `Beta Limited` — needs a pattern and, more
importantly, the chunk that asserted it, so every edge can be checked.

## Checklist for this part

- [x] organisations by suffix, people by honorific, places by preposition
- [x] sentence breaks end a run (tested)
- [x] connectors inside a name (tested)
- [x] leading articles dropped (tested)
- [x] spans returned, so a caller can highlight the source text
- [x] confidence orders the kinds, and the ordering is asserted
- [x] benchmarked: 63,855 chars/s over the corpus
- [ ] relations between the entities (Part 23)
