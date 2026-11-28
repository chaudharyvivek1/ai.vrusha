# Part 2 (and 3) — the 40-question set, and the loader that refuses a bad one

**Wave 38 · `vrusha/eval_sets/docs-40.json`, `vrusha/evaluate.py`, `vrusha evaluate --set`**

## What was built

**40 labelled questions** about this repository's own documentation, each with
the document that answers it and the words a correct passage must contain.
Phrased away from the target's own words, so a question tests retrieval rather
than the keyword index.

**A loader that refuses a malformed set** — `load_set()` raises `MalformedSet`
with the item number and the reason:

| refused | why |
|---|---|
| a question with no document | it would score as a miss forever |
| a question with no keywords | its passage can never be found, so `passage@k` silently measures the labels |
| a blank keyword | same, one word at a time |
| a duplicate question | it is counted twice and weights itself |
| invalid JSON, an empty set, a missing name | the failure says which sets exist |

**`vrusha evaluate --set docs`** — Part 3 in the same wave, because a set nobody
can run is a file, not a measurement.

## The numbers, on 40 questions

| metric | 12 questions (Part 1) | 40 questions (Part 2) |
|---|---|---|
| recall@1 | 25% | **35%** |
| recall@5 | 75% | **70%** |
| MRR | 0.390 | **0.468** |
| nDCG@10 | 51% | **57%** |
| passage@5 | 42% | **48%** |

The larger set is *harsher* on recall@5 (70% vs 75%) and kinder on everything
else. That is what a bigger, more varied set does: it finds questions the first
twelve did not cover, and it stops the average being dominated by the easiest
ones. **The 12-question set was flattering**, which is the argument for Part 2
existing at all.

## What was pending

- Cost per question (Part 4)
- BEIR / CRAG subsets (Part 5)
- Passage-level labels beyond keywords — a real relevance grade, not a word check

## Errors found and fixed

**1. Two keywords did not appear in their own documents.** Found by *checking*
before committing, with a script that reads each document and looks for each
keyword: `ADDRESS.md` says "collide", not "collision"; `OPERATIONS.md` never says
"drift" or "changed". Both were replaced with words the documents actually use.
This is now a permanent test — `test_every_keyword_appears_in_its_own_document` —
because a keyword that is not there makes `passage@k` unachievable and turns the
metric into a measure of the labels.

**2. The eval set would not have shipped in the package.** `pyproject.toml`
lists package data explicitly (`rules/levels/**`, `languages/profiles/*`), and a
new `eval_sets/` directory was not in it — so `pip install vrusha` would have
produced a package whose `vrusha evaluate` said *"no such set"*. Found by asking
"where does this file live after install?" and then **building a wheel to check**:

```
wheel: vrusha-0.1.0-py3-none-any.whl
  eval set   True
  rules      True
  profiles   True
```

## Found while rechecking the previous work

**3. `vrusha/store/pdf.py` tracked `in_text` and never read it.** `ruff` flagged
it as an unused assignment, and an unused variable in a parser usually means a
value was computed and dropped. It was: `BT`/`ET` set the flag, but `Td`, `TD`
and `Tm` moved the text cursor regardless of whether a text object was open — so
a positioning operator in a graphics block would shift the next text run. The
flag now gates the positioning operators, and the PDF tests still pass.

**4. `vrusha evaluate` on an empty store printed a table of zeros.** That reads
as "retrieval is broken" rather than "the database is empty". It now refuses with
the fix in the message.

## What is next

**Part 4: cost accounting.** Every metric here is quality; none is cost. The plan
says report cost per completed question, and there is nothing yet that measures
tokens, seconds or bytes per answer.

After that, **Phase 2 (reranking)**, because the 40-question run says the misses
are ranking failures — three of them are now measured, not guessed.

## Checklist for this part

- [x] 40 questions, each with a document and keywords
- [x] every keyword verified present in its document, as a test
- [x] loader refuses: no document, no keywords, blank keyword, duplicate, bad JSON, empty, missing
- [x] `--set` on the CLI, with the set name in the table title
- [x] the set ships in the built wheel (verified by building one)
- [x] numbers recorded, and compared against the 12-question run
- [ ] cost per question
- [ ] external datasets
