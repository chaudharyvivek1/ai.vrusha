# Part 40 — fuzzing, and the programme closed

**Wave 64 · `tests/test_fuzz.py`, `bench/registry.py`**

## What was built

**44 fuzz cases** across every parser: HTML extraction, CSV, JSON-LD, the
blackboard, the safety scanner, the maths canonicaliser, the JSON API and the BEIR
loader. Each is fed deterministic rubbish — random printable text, broken markup,
binary noise, wrong types, malformed JSON bodies.

The rule the tests assert: **a parser may return a value or raise a declared error.
Nothing else.** A `TypeError` from deep inside a parser is a bug, because a caller
cannot catch what it cannot name.

## The benchmark

```
payloads=40  crashes=0  ms=254.1  per_second=157.4
```

The throughput number is unremarkable. **`crashes=0` is the point** — a parser that
survives fuzzing is one a caller can trust with a page it did not choose.

## What was found

**Nothing crashed.** Every one of the 44 cases passed on the first run, and the
reason is the shape of the code rather than luck: this project's parsers were
written to return `[]` or `{}` on rubbish from the first wave — `extract_document`
returns counts, `jsonld` returns a list, `scan` returns findings, `beir.load` raises
`BeirError` with the line number.

**The one thing that would have crashed and did not:** the API. Four malformed
bodies (`{`, `[]`, `{"run": 123}`, `\x00\xff`) all produce a **4xx**, not a 500 and
not a hang — which is the fix from Part 33 holding under fuzzing, three parts later.

## What this does not cover

| gap | why |
|---|---|
| PDF parsing | needs a real PDF stream; the fixture covers the happy path only |
| concurrent mutation | one writer at a time is the design, so fuzzing it proves little |
| adversarial *sizes* | inputs are bounded at 800 chars; a 500 MB page is untested |
| the OCR and browser seams | no binary to fuzz |

## The programme, closed

All 40 parts of `docs/PLAN-V3.md` are done or explicitly declined:

| phase | result |
|---|---|
| 0 measurement | 5/5 — metrics, 40 questions, cost |
| 1 documents | **0/8 — not started** |
| 2 reranking | 4/4 — lexical adopted at 0.3, cross-encoder not adopted |
| 3 incremental indexing | 4/4 — 24× faster appends, upsert, compaction, snapshots |
| 4 graph | 5/5 — entities, relations, communities, paths, bounded boost |
| 5 compaction | 4/4 — 23% of the tokens, verified, offload, anticipation |
| 6 orchestration | 5/5 — the loop, the pool, remote agents, the honest comparison |
| 7 gaps | 3/3 — three seams, each declining by default |
| 8 hardening | 2/2 — the injection surface, fuzzing |

**Phase 1 was never started, and that is the honest summary of the programme:** the
plan put documents after measurement and the measurement kept being more useful.

## What is next

Not another part. The next work is the one this programme kept deferring:

1. **a corpus that is not documentation** — the graph parts (23, 26) and the
   orchestration comparison (35) all concluded "this corpus cannot exercise it";
2. **Phase 1's eight document skills**, which is the largest untouched block;
3. **a machine with a browser and Tesseract**, to turn three seams into measurements.

## Checklist for this part

- [x] 44 fuzz cases across every parser (all passing)
- [x] deterministic seeds, so a failure is reproducible
- [x] the API returns 4xx for four malformed bodies
- [x] the blackboard refuses an unknown kind by name (tested)
- [x] the BEIR loader refuses rubbish with `BeirError` (tested)
- [x] benchmarked: 40 payloads, **0 crashes**, 157/s
- [x] what it does not cover, written down
