# Changelog

Every change that landed on `main`, in the order it landed, with what it did and
what it cost. Waves 1–5 predate this file; they are the harvest, address, passes,
gate and model layers the rest builds on.

Format: **what changed** · **new files** · **the bug the tests or the demo found**.

---

## Wave 60 — Part 36: JavaScript-rendered pages · PR #56

- `vrusha/harvest/render.py` — `NullRenderer` (default, declines), `PlaywrightRenderer`
  (optional, lazy), free shell detection by text-to-markup ratio, and
  `maybe_render()` which always returns a sentence saying what happened
- `vrusha/config.py` — `harvest.render.backend: none`
- `bench/registry.py` — `render`; `docs/benchmarks/24-render.md`
- `tests/test_render.py` — 13 tests

**Measured:** detection costs **26 µs on a shell, 116 µs on a real page** — a
rounding error next to a 9 ms retrieval, which is the property that matters: a check
that cost a millisecond would be a tax on every fetch to save a few.

**The browser is not installed here, so its cost and success rate are unknown.** This
part is a **seam, not an adoption** — the same distinction as the cross-encoder in
Part 16. The default stays `none`, because turning it on would be a 300 MB decision
made on no evidence.

---

## Wave 59 — Part 35: four levels against one agent · PR #55

- `vrusha/compare.py` — the same 40 questions through a single loop and through the
  hierarchy, same embedder, same retrieval
- `bench/registry.py` — `compare`; `docs/benchmarks/23-compare.md`
- `tests/test_compare.py` — 5 tests

**The answer: quality is identical** (recall@5 0.475 both), and it must be, because
both call the same `retrieve()`. For single-shot questions a hierarchy cannot help —
there is no step 2 that needs step 1's output.

**The trap I did not take:** the orchestrated run looks **faster** (0.62×), but it
was the 4-worker pool, not the hierarchy. Put the pool on the single loop and you get
the same speed with **2 board entries instead of 42**. Reporting "orchestration is
1.6× faster" would have been the most misleading true sentence in this repository.

**Cost, concretely:** 42 board entries, 40 claims, up to 8 slots — for zero quality
change.

**Phase 6 closed.** The hierarchy is built, tested and measured; its justification is
a multi-step corpus this project does not have yet, and the work log says so.

---

## Wave 58 — Part 34: the loop over HTTP · PR #54

- `vrusha/orchestrator.py` — `run_remote()`: the same loop with every board and
  claim call over HTTP; an L4 agent on another machine claims, works locally,
  reports back
- `bench/registry.py` — `remote_loop`; `docs/benchmarks/22-remote-loop.md`
- `tests/test_orchestrator.py` — 6 more tests

**Measured:** 8 tasks, 4 workers — **58 ms in-process against 227 ms over HTTP,
3.9×**. Three round trips per task at ~6.5 ms each, and the arithmetic matches.

**The number to design against:** if the work per task is under ~20 ms, the wire
dominates and a remote agent is a loss. Distribution pays when the *worker* is
expensive, not when it is cheap. Loopback is the best case; a real network pays more.

---

## Wave 57 — Part 33: remote agents, and the bug that stopped it · PR #53

- `vrusha/remote.py` — `RemoteBoard`: `append`, `since`, `claim`, `complete`,
  `agents` over HTTP
- `vrusha/serve.py` — `GET/POST /board`, `POST /claim`, `POST /complete`,
  `GET /agents`; one board created at startup, not one per request
- `bench/registry.py` — `remote`; `docs/benchmarks/21-remote.md`
- `tests/test_remote.py` — 13 tests

**The bug was not SQLite.** Two hypotheses — a second connection blocking, then a
per-request connection — were both disproved by measurement. The cause was found by
logging each stage of `do_POST` to a file: **`do_POST` reads the body once at the
top, and my `/board` branch called `self._body()` a second time.** On a drained
stream, `rfile.read(length)` blocks forever, the handler never writes a response,
and the client hangs.

**The fix is one line per route:** use the `body` already read. `POST /board` went
from hanging to **201 in 6.5 ms**; the nine remote tests went from 75 s of timeouts
to **13 tests in 7.2 s**.

**Measured:** remote append 6.55 ms against 1.29 ms in-process — 5×, not 50×.

---

## Wave 56 — Part 32: the bounded worker pool · PR #52

- `vrusha/orchestrator.py` — `run_pool()`, the same loop over a `ThreadPoolExecutor`
  of `workers` (default 4); every task still claims, works, records and releases
- `bench/registry.py` — `pool`; `docs/benchmarks/20-pool.md`
- `tests/test_orchestrator.py` — 5 more tests

**Measured on 8 tasks of 20 ms each:**

| workers | wall clock | speedup |
|---|---|---|
| 1 | 238.95 ms | 1.00× |
| 4 | **64.45 ms** | **3.71×** |
| 8 | 49.40 ms | 4.84× |

**The curve flattens and the flattening is the finding.** Four workers are close to
linear; eight give 4.84×, not 8×, because every board write takes one lock — the
bookkeeping cannot speed up, only the waiting can. `docs/AGENTS.md` says 64 logical
agents over a *small* pool, and this is the measurement that says small.

---

## Wave 55 — Part 31: the orchestration loop · PR #51

- `vrusha/orchestrator.py` — L1 plans, L2 decomposes, L3 claims and works, L4
  reports; the worker is **injected**, so the loop is tested without a model or a
  network; one retry then failed; every task ends in a state on the board and in
  the claims
- `bench/registry.py` — `orchestrator`; `docs/benchmarks/19-orchestrator.md`
- `tests/test_orchestrator.py` — 14 tests

**Measured:** the loop's own overhead is **7 ms per task** with a worker that does
nothing — against a 9 ms retrieval, so bookkeeping is comparable to work at this
scale.

**The bug the benchmark found:** `run_id` was `f"run-{int(time.perf_counter())}"`,
which **collides for every run in the same second**. The second run found the first
one's claims already `done`, was refused, and marked every task failed. The unit
tests never saw it because each passes an explicit `run_id`. **A test suite that
supplies its own ids cannot catch an id collision** — that is what a benchmark
harness is for.

---

## Wave 54 — Part 30: anticipation, Phase 5 closed · PR #50

- `vrusha/store/anticipate.py` — `predict()` (open questions, then unclaimed tasks),
  `warm()` under a **500 ms budget** and at most **3 tasks**, `lookup()`, and a
  bounded expiring cache (32 entries, 60 s TTL)
- `vrusha/store/blackboard.py` — `since_of_kind()`, so a prediction does not scan
  the whole log
- `bench/registry.py` — `anticipate`; `docs/benchmarks/18-anticipate.md`
- `tests/test_anticipate.py` — 12 tests

**Measured:** a cache hit costs **0.8 µs** against a ~9 ms retrieval — the saving is
the whole query and the bookkeeping is free.

**Every part is capped**, because anticipation is a bet: 3 tasks, 500 ms, 32 entries,
60 s. A test sets `budget_ms=0` and asserts nothing is warmed — the budget is a hard
stop, not a target.

**Bug:** `since_of_kind()` called `self._entry(row)` for a module-level `_row()`
helper. The method raised, `predict()` swallowed it, and prediction silently
returned nothing — the swallowing is deliberate (a prediction must never break a
run) and it also hid the bug, which is the cost of that choice.

**Phase 5 closed:** 7,579 tokens of run history became 1,749; a 25,000-token page
became a pointer; the next query costs 0.8 µs instead of 9 ms when the guess was
right.

---

## Wave 53 — Part 29: offload · PR #49

- `vrusha/store/compaction.py` — an entry past 20,000 tokens is appended to the
  blackboard as an `artifact` and replaced **in place** by a one-line reference
  (`[offloaded seq 7 -> board:417, 25,000 tokens]`); order is preserved, the full
  text stays readable
- `bench/registry.py` — offload added to `compaction`
- `tests/test_compaction.py` — 5 more tests

**Measured:** an oversized entry becomes a reference in **3.72 ms**, and the token
ratio rounds to **0.0**.

**A reference beats a truncation:** `board:417` can be followed; a truncated
20,000-token page is a shorter page with a hole in it, and nothing says where.

---

## Wave 52 — Parts 27 and 28: compaction with verifiable summaries · PR #48

- `vrusha/store/compaction.py` — compact at **85%** of a budget; offload a single
  entry past **20,000 tokens**; a rule-based summary keeping every decision, error,
  question and artifact reference plus the last finding per task, dropping progress,
  and keeping the last five entries verbatim
- **every summary carries the `seq` range it replaced**, and `verify()` checks the
  summary against the entries in that range
- `bench/registry.py` — `compaction`; `docs/benchmarks/17-compaction.md`
- `tests/test_compaction.py` — 14 tests

**Measured:** 53 entries, **7,579 → 1,749 tokens (23%)**, verified, in 0.13 ms.

**The bug that shaped the design:** the first summary was **2.7× larger** than its
input, because with six small entries the header and kept categories outweighed the
text. Compaction now **declines** when it would grow the text — `useful=False`,
`text=""`, caller keeps the entries — because compaction that grows the text has
made the caller slower and no better off.

**Why verification is the feature:** a summary that silently drops a fact produces
confident wrong answers. Two tests break a summary deliberately and assert
`verify()` fails.

---

## Wave 51 — Part 26: bounded graph ranking, Phase 4 closed · PR #47

- `vrusha/store/graphboost.py` — a chunk whose entities share a community with the
  query gets a multiplicative boost, **capped at 15% and at 5 hits**; off by default
- `vrusha/store/query.py` — wired behind `graph.boost`
- `bench/registry.py` — `graphboost`; `docs/benchmarks/16-graphboost.md`

**Measured on the 40 questions: no metric changes at all** — recall@1, recall@5,
nDCG@10 and passage@5 are identical with the boost on. The reason is Part 23's
finding: `docs/` produces **zero edges**, so there is no graph to boost with.

**The verdict: built, bounded, off by default, unproven on this corpus.** Turning it
on would be a preference, not an improvement — the same bar the lexical reranker
cleared and the cross-encoder failed.

**The benchmark demonstrated its own caveat:** the first fixture was a *chain*, which
label propagation merges into one community, so every hit got the same boost and
nothing reordered — exactly the "a constant reorders nothing" line already in the
module docstring.

**Phase 4 closed:** the graph machinery works and this corpus cannot exercise it. A
corpus of business or news prose would, and the parts are ready for one.

---

## Wave 50 — Part 25: multi-hop paths · PR #46

- `vrusha/store/paths.py` — breadth-first path search, `max_hops` enforced, every
  hop carrying the sentence that asserted it; path confidence is the **weakest**
  hop; `expand()` returns everything within N hops, nearest first
- `bench/registry.py` — `paths`; `docs/benchmarks/15-paths.md`
- `tests/test_paths.py` — 12 tests

**Measured:** a two-hop search over a 199-edge chained graph costs **2.77 ms**.

**Two properties, both tested:** every hop carries its evidence (a path is only as
good as its weakest edge, so the confidence is the minimum, not the average), and
the hop cap is a hard limit — a 3-hop path is **not found** at `max_hops=2`, because
two hops is a plausible claim and five is "everything is connected".

**My own demonstration was wrong:** I expected `Meera Rao → Acme Corporation` to
have a path; they are in two disjoint components. The code was right and the example
was wrong — a graph saying "no path" is usually telling the truth about the data.

---

## Wave 49 — Part 24: communities by label propagation · PR #45

- `vrusha/store/communities.py` — label propagation over the entity graph; nodes
  visited in sorted order, ties to the smaller label, ids renumbered densely;
  `build()`, `community_of()`, `members()` ordered by degree
- `bench/registry.py` — `communities`; `docs/benchmarks/14-communities.md`
- `tests/test_communities.py` — 11 tests

**Measured:** a synthetic graph of 20 clusters × 10 members with no cross-links —
**20 of 20 communities found, in 1.19 ms.** A known-shape graph is the only honest
benchmark here: "some number of communities" on real text proves nothing without a
gold partition.

**Determinism was designed in, not discovered:** the first sketch used
`max(counts, key=counts.get)`, which returns the first key in insertion order and
so varies with how the edges arrived. It was replaced with an explicit smallest-label
tie-break before any test ran, and five repeat runs are now asserted to agree.

---

## Wave 48 — Part 23: relations with evidence · PR #44

- `vrusha/store/edges.py` — nine business predicates, each edge carrying **the
  sentence that asserted it**, stored in `x_edges` with confidence and doc/chunk
  id; `neighbours()` walks one hop in both directions
- `bench/registry.py` — `edges`; `docs/benchmarks/13-edges.md`
- `tests/test_edges.py` — 15 tests

**The benchmark reports a zero, and the zero is the finding:** the documentation
corpus produces **0 edges**, because the patterns are business relations and
`docs/` is technical prose about a retrieval engine. A fixture corpus reports
**5 edges, all 5 with evidence** — so the extractor works and the corpus has no
relations in it. Reporting the zero rather than hiding it is the same rule as the
`model2vec=0` flag.

**Bug:** `Acme acquired March` — a month became the object of an acquisition,
because `in March` matches the place-preposition rule and carried 0.7 confidence.
Fixed with a calendar stoplist, with the reason written beside it.

---

## Wave 47 — Part 22: entities from prose · PR #43

Phase 4 begins. The store could read tables and not sentences.

- `vrusha/store/prose.py` — rule-based extraction: organisations by suffix, people
  by honorific, places by preposition, spans and confidence returned
- `bench/registry.py` — `entities`; `docs/benchmarks/12-entities.md`
- `tests/test_prose.py` — 13 tests, half of them refusals

**Measured:** 1,394 entities across the documentation corpus at **63,855
characters per second**.

**Three bugs, all found by running it on one real sentence:** a run crossed a
sentence break (`Pune. Dr Meera Rao` became one entity, because the period sat
inside the previous token); `Reserve Bank of India` split at `of`; and `The
Reserve Bank of India` kept its article.

**What it will not do is written into the module docstring** — an organisation with
no suffix and no capital, `Jordan` the person against `Jordan` the country,
coreference. Those are model jobs, and a caller who knows the limits can trust
what they get.

---

## Wave 46 — Part 21: versioned snapshots, and Phase 3 closed · PR #42

- `vrusha/store/snapshot.py` — a **generation** in a `CURRENT` file bumped after
  every swap; a **pin** naming a generation a reader is using; **gc** that deletes
  old generations except the pinned and the newest few
- `safe_compact()` archives the generation it replaces, publishes a new one, and
  collects — so a reader that pinned the old generation keeps its files
- `bench/registry.py` — `snapshot`; `docs/benchmarks/11-snapshot.md`

**The rule, in one sentence: a pinned generation is never deleted.** A long query
or a slow reader holds a snapshot for as long as it likes without a compaction
pulling the files out from under it.

**Measured:** pin 2.29 ms, read `CURRENT` 0.47 ms, gc 1.26 ms — cheap enough to
take one pin per query.

**Phase 3 is closed:** delta segments (24× faster than a rebuild), upsert and
delete with tombstones, compaction with a breakable lock, and snapshots.

---

## Wave 45 — Part 20: compaction, and a benchmark report per subsystem · PR #41

- `vrusha/store/delta.py` — `safe_compact()` builds the new base in `<index>.new`
  and moves the files over one at a time; a lock file (`O_EXCL`) allows one
  compactor at a time; a lock older than `stale_lock_s` is **broken**, because a
  lock nobody releases is an outage
- `docs/benchmarks/` — **ten reports**, one per subsystem, generated from the
  harness, each with its numbers, its method and how to reproduce it
- `bench/registry.py` — `index_delta` now measures compaction too

**Measured:** compaction 354.56 ms against a rebuild's 350.01 ms — it *is* the
expensive operation, which is why the delta verbs (~20 ms, **16.6× cheaper**) make
deferring it worth doing.

**The recheck found the sharpest version of the project's oldest rule.** The
`retrieval` benchmark did not record which embedder ran: on a pod without
model2vec it falls back to the deterministic hash and `recall@5` reads **0.47**
where the same set scores **0.70**. Same code, same corpus, a third of the
quality, and nothing in the output said which. It now reports `model2vec=1|0`.

---

## Wave 44 — Part 19: upsert and delete, and two old bugs they exposed · PR #40

- `vrusha/store/delta.py` — `upsert()` writes the new vector **and** a tombstone;
  `delete()` writes only a tombstone; the merge settles each chunk id at its
  newest mention, newest segment first
- `vrusha/store/db.py` — `iter_vectors()` now joins `chunks` and skips
  `active = 0`
- `bench/registry.py` — `index_delta` measures upsert and delete too
- `tests/test_delta.py` — 7 more tests

**Measured:** `append 14.06 ms · upsert 13.71 ms · delete 16.34 ms` against
`rebuild 442.92 ms` — **31×**. An upsert costs the same as an append.

**Two bugs, both in older code, both found by this part:**

1. **`load_segments()` filtered on `rows`**, so a delete-only segment — which has
   zero vectors by definition — was discarded and its tombstones with it. Deleted
   chunks came back.
2. **A deactivated chunk survived every index rebuild.** `iter_vectors()` never
   joined `chunks`, so `AnnIndex.build()` indexed retired chunks too, and
   compaction resurrected them. This predates delta and would have stayed
   invisible without a delete path to expose it.

---

## Wave 43 — Part 18: delta segments, 24× faster than a rebuild · PR #39

Phase 3. An append no longer rewrites the index.

- `vrusha/store/delta.py` — a base index plus append-only segments; `append()`
  writes one small segment and never opens the base for writing; `search()`
  merges base and segments by cosine; `should_compact()` / `compact()` fold them
  back; a corrupt segment is skipped, not fatal
- `bench/registry.py` — `index_delta`, append against a rebuild
- `tests/test_delta.py` — 9 tests

**Measured:** adding 200 vectors to a 4,000-vector index costs **15.9 ms against
383 ms — 24× faster** — and a test asserts the base files' mtimes are unchanged.
Merged search matches the exact scan (recall@5 = 1.0) and costs 8.2 ms.

**The design trade, stated:** segments are brute-forced, because they are small by
construction and brute force over a few thousand rows beats building routing
structures for them. That is why the segment count is capped — past the cap,
compaction stops being an optimisation and becomes a latency requirement.

**Bug:** `np.save` silently appends `.npy` to a string path, so the per-file
rename looked for a file that did not exist. The segment is now written to a
temporary directory and renamed whole, which is more atomic anyway.

---

## Wave 42 — Part 16: the cross-encoder, built and not adopted · PR #38

- `vrusha/rerank.py` — `CrossEncoderReranker` over **ONNX, not torch** (15 MB
  instead of 800 MB), batched scoring, `scorer` injectable so the logic is
  testable without the model
- `retrieve()` — the second stage now sees `rerank.window` (20) of the first
  stage's list, not all 60 candidates

**The verdict: not adopted, and the reason is the machine.** The model loads
(23 MB quantised MiniLM) and then: it first **OOM'd allocating 755 MB** per batch,
and after the window fix it **timed out** on twelve questions on two shared vCPUs.
The bar was explicit — beat nDCG 0.611 without losing passage ranking — and
without a number there is no adoption.

**A real bug found by measuring:** the reranker was handed every candidate. A
second stage is a *re-ranker*, not a second search; handing it everything was both
slower and fatal.

**A finding that argues for the 40-question set:** on the first 12 questions the
lexical reranker improved *everything* (nDCG 0.477 → 0.598, passage@5 0.333 →
0.500); on all 40 it costs passage ranking. The same code, opposite conclusions,
from the size of the set — a small set does not just give a noisy number, it gives
a confidently wrong one.

---

## Wave 41 — Parts 15 and 17: the lexical reranker, and every work benchmarked · PR #37

- `vrusha/rerank.py` — a field-weighted BM25 reranker: no model, no dependency,
  ~2 ms for twenty candidates, `alpha` swept rather than guessed
- `bench/registry.py` — **one benchmark per subsystem**, runnable as a table
- `tests/test_benchmarks.py` — fails CI when a module has neither a benchmark nor
  a written reason to be exempt
- `docs/work/part-15-lexical-rerank.md`, `docs/BENCHMARKS.md`

**The measurement:** at `alpha=0.3` nDCG@10 rises 0.576 → **0.611** and recall@5
70% → **72.5%**, while **passage@5 falls 45% → 42.5%** and latency doubles
(29 → 55 ms). Document ranking improves, passage ranking degrades — monotonically,
up to −22.5 points at alpha 1.0 — because a document-level signal cannot tell one
paragraph from another inside the same file.

**A hypothesis tested and disproved:** the loss is not title bias. Weighting the
title 1×, 2× or 3× gives the same nDCG to three decimals. Alpha is the only lever.

**The decision:** opt-in at 0.3, default `none`. A +3.5-point gain for 2× latency
and a small passage loss is worth having when asked for, not by default.

**The benchmark rule, enforced:** the coverage test found **39 modules with no
benchmark** on its first run. Nine are now measured; the rest carry a sentence
explaining why a benchmark would measure the wrong thing.

---

## Wave 40 — Part 14: the reranker seam · PR #36

Phase 2 begins. A seam, not a reranker: a protocol, a no-op default, a registry,
and three safety rules.

- `vrusha/rerank.py` — `Reranker` protocol, `NoOpReranker`, `RERANKERS` registry,
  `build_reranker(cfg)`, `apply()`
- `retrieve(..., reranker=, rerank_off=)`; `vrusha evaluate --no-rerank`
- `tests/test_rerank.py` — 15 tests

**Three rules, because a second stage is a new failure mode in the query path:** a
reranker may reorder and drop but may not **invent** a hit it was not given; a
reranker that raises degrades to the fused order with a note; and `rerank_off`
beats both the config and an explicitly passed reranker, because a comparison that
can be overridden is not a comparison.

**The acceptance test passes:** the no-op produces byte-identical results, and
`reranked_by` stays empty — a no-op is not a reranker that ran.

**Bug found by the test:** `rerank_off` only suppressed the *configured*
reranker, not one passed in explicitly.

---

## Wave 39 — Part 4: cost accounting · PR #35

Phase 0 is complete: quality is measured on 40 questions, and cost is measured on
the same run.

- `vrusha/evaluate.py` — `context_tokens`, `answer_tokens`, `seconds`, `usd` per
  question, and a cost block in the table
- `cost per question` **and** `cost per answered question` — a system that refuses
  half its questions is cheap per question and expensive per answer
- prices default to **zero** and the table prints `local`, because the default
  answerer is extractive and calls no model
- `tests/test_evaluate.py` — 6 more tests against hand arithmetic

**Measured:** 986 context tokens and 232 answer tokens per question, 0.03 s, no
token cost. With `--answers`, citation accuracy is **57%** — the answer cites the
document that holds it slightly more often than retrieval finds it at rank 1.

**One error, in my own test:** `context_tokens` is a mean over all questions, so
100 tokens beside a refused question that retrieved nothing averages to 50. Third
time in this project that the test expectation, not the code, was wrong — the
pattern is writing the expectation from the intent instead of the arithmetic.

---

## Wave 38 — Parts 2 and 3: the 40-question set, and the work log · PR #34

- `vrusha/eval_sets/docs-40.json` — 40 labelled questions over this repository's
  own documentation, each with its document and the words a correct passage needs
- `vrusha/evaluate.py` — `load_set()` refuses a malformed set with the item
  number and the reason (no document, no keywords, blank keyword, duplicate,
  bad JSON, empty, unknown name)
- `vrusha evaluate --set docs`; `docs/work/` — the checklist and one file per part
- `pyproject.toml` — `eval_sets/*.json` added to package data

**The numbers, on 40 questions:** recall@1 **35%**, recall@5 **70%**, MRR
**0.468**, nDCG@10 **57%**, passage@5 **48%**. The bigger set is *harsher* on
recall@5 than the 12-question one (70% vs 75%) — the first twelve were flattering,
which is the argument for Part 2 existing.

**Bugs found before shipping:** two keywords did not appear in their own
documents (`ADDRESS.md` says "collide", not "collision"), which would have made
`passage@k` unachievable; and the new `eval_sets/` directory was missing from
`package-data`, so the set would not have shipped in a wheel — found by building
one and looking inside.

**Found while rechecking the previous work:** `pdf.py` tracked `in_text` and never
read it, so `Td`/`Tm` moved the text cursor even outside a text block; and
`evaluate` printed a table of zeros for an empty store instead of refusing.

---

## Wave 37 — Part 1: the metrics · PR #33

The first of the forty parts in `docs/PLAN-V3.md`, and the one everything else is
judged by.

- `vrusha/evaluate.py` — recall@k, precision@k, MRR, **nDCG@k**, passage@k,
  **citation accuracy**, **faithfulness** (as a labelled proxy), and a summary
  renderer that defines every number beside it
- `vrusha evaluate [--answers] [--json] [--verbose]`
- `docs/EVALUATE.md`; `tests/test_evaluate.py` — 23 tests against hand arithmetic

**The first measured numbers for the whole pipeline**, on this repository's own
documentation: recall@1 **25%**, recall@5 **75%**, MRR **0.390**, nDCG@10 **51%**,
passage@5 **42%**. Worse than the retrieval-only figures in `BENCHMARKS.md`, which
is the point of measuring.

**Two numbers deliberately not numbers.** Citation accuracy prints `n/a` when
nothing was cited — the code returns `None`, because "cited nothing" and "cited
the wrong thing" are different failures. Faithfulness is a lexical proxy and is
labelled one everywhere.

**Bugs found by running it:** the table printed `citation_accuracy 0%` for a run
that cited nothing (the exact confusion the `None` exists to prevent), and
`--verbose` collided with the shared parent parser.

---

## Wave 36 — Plan V3: the review, the repo, and forty parts · PR #32

An outside review of the repository (28 November 2026) was read in full and
compared against the code, point by point.

- `docs/PLAN-V3.md` — what the review says, **what the repo actually contains**,
  where the three plans agree and conflict, and forty parts in nine phases
- `docs/README.md`, `PROGRAM.md`, `README.md` — pointers

**What the review got right and the repo had already closed:** the hash embedder
(wave 25), PDF tables (wave 12), entity resolution (wave 26).

**What it got right and the repo had not:** reranking, incremental indexing,
community detection, multi-hop, nDCG/citation/faithfulness metrics — and
**JavaScript-rendered pages**, which my own plan had understated.

**Where it changed my mind:** `MASTER-PLAN.md` said "do not adopt an external
benchmark". That was aimed at saturated *leaderboards* and was too broad — BEIR
and CRAG are **datasets**, and using a subset is legitimate and cheap.

**Where I disagree:** the cross-encoder is not P0 — its benefit is unmeasurable
before an eval set exists, and every cross-encoder pulls torch or ONNX. Measure
first (Phase 0), then the seam, then the model, and only adopt it if it beats a
lexical reranker.

**The order that follows:** measurement → documents → reranking → incremental
indexing → graph → compaction → orchestration → the review's gaps → hardening.

---

## Wave 35 — the 64-slot registry, and the documentation indexed · PR #31

Step 3 of `docs/AGENTS.md`, plus the documentation made navigable.

- `vrusha/store/registry.py` — 64 pre-created slots per run, allocation in one
  atomic `UPDATE … RETURNING`, heartbeats, a sweep for agents that stopped
  beating, and a cap that **refuses** the 65th agent
- `vrusha agents --run X [--sweep] [--stale-after]`
- `docs/README.md` — every document, indexed by what it answers
- `docs/BOARD.md` — the registry section; `tests/test_registry.py` — 17 tests

**The cap is the point:** a limit that lives in a document is a limit that does
not exist. The 65th registration is refused with the count in the reason.

**The audit found nothing in the module** — not because it is simpler, but
because the lesson from waves 33 and 34 was applied before writing it: every
method holds the lock across the statement *and* the read that follows it. The
thread and process tests passed first time. The only fix was one long line.

**The audit did find something in the tests.** Eight `DeprecationWarning`s:
`fork()` from a multi-threaded process "may lead to deadlocks in the child" —
a lock another thread held at the moment of the fork is duplicated into the
child and never released. All three process tests now use `spawn`, which also
stops them inheriting a parent connection — the bug that made the blackboard's
multi-process test pass alone and fail in the suite.

---

## Wave 34 — claims and leases · PR #30

Step 2 of `docs/AGENTS.md`: one agent per task, and a lease so a dead agent lets
go.

- `vrusha/store/claims.py` — `claim`, `renew`, `release`, `complete`, `reap`,
  `summary`, `list`; one `INSERT … ON CONFLICT … WHERE … RETURNING` does the
  arbitration, so there is no read-then-write gap
- `vrusha claims --run X [--state] [--reap]`
- `tests/test_claims.py` — 17 tests, including 64 threads racing for 10 tasks and
  6 processes doing the same

**The rules, each with a test:** a live lease is never broken; an expired one is
taken over; `done` is final; `failed` is retryable; only the holder renews or
completes; `attempts` accumulates so a poison task is visible; a zero-duration
lease is refused.

**One bug, caught by the acceptance test:** the first version locked the write and
then read the row back **outside** the lock. Sixty-four threads produced
`sqlite3.InterfaceError: bad parameter or other API misuse` — two threads on one
connection. Same class as the blackboard's bug, one level up: serialising the write
is not enough if the code then reads on the same handle.

---

## Wave 33 — the blackboard · PR #29

Step 1 of `docs/AGENTS.md`: the shared log the agents will read and write.

- `vrusha/store/blackboard.py` — append-only entries, dense per-run `seq`,
  delta reads, subtree scoping, replay, and **quarantine** for text that looks
  like an instruction aimed at a reader
- `vrusha board` — runs, entries, `--since`, `--all`
- `docs/BOARD.md`; `tests/test_blackboard.py` — 25 tests

**The acceptance test:** 1,000 appends from 8 threads *and* 1,000 from 4 separate
processes, each producing 1,000 distinct `seq` values, dense and lossless.

**Three bugs in the first version, found by auditing it the same day:**

1. **A shared connection.** The board used the store's `sqlite3.Connection`; eight
   threads produced **127 of 1,000** entries with `cannot open savepoint - SQL
   statements in progress`. A connection is not safe for concurrent statements,
   whatever `check_same_thread` says. It now owns its own connection and
   serialises writers, because SQLite has one writer at a time.
2. **Appends were never committed**, so a second process saw an empty board.
3. **`refs="table:1"` became seven refs** — a string is iterable and the coercion
   was silent.

A fourth was in the test: it left a `Blackboard` open, and `fork` copied that
connection into every child — which is why the multi-process test passed alone and
failed under the full suite.

---

## Wave 32 — the master plan · PR #28

The audit, the state of the field, and the agent design, merged into one plan.

- `docs/MASTER-PLAN.md` — the benchmark landscape, the context-management
  primitives, a **70-skill inventory**, old plan vs new evidence, and six phases
- `docs/PROGRAM.md`, `README.md` — pointers

**What the research changed.** The public agent benchmarks are saturated
(OSWorld-Verified 90.19% against a 72.36% human baseline; GAIA 94.0% against 92%)
or contaminated (SWE-bench Verified was withdrawn by OpenAI in February 2026 after
an audit found 59.4% of audited problems had flawed tests and every frontier model
could reproduce gold patches). The frontier moved to long horizons — OSWorld 2.0
sits at 20.6% — and to cost. So this project will not adopt an external benchmark;
phase 5 builds a long-horizon task set of its own and reports cost per completed
task.

**What the inventory changed.** 70 skills: **26 built, 14 partial, 30 gaps**. The
gaps are not where the old plan assumed — extraction is complete, and the holes are
documents/office (8 of 8 missing), media, and agent infrastructure. So documents and
**context compaction** now come before orchestration: 64 agents with no compaction
is 64 agents that each run out of window.

**Two numbers adopted from the field rather than invented:** offload a tool result
to storage past 20,000 tokens, and compact at ~85% of the window. Both now have a
home in phase 2.

---

## Wave 31 — second audit of the whole package · PR #27

Run before designing the next phase: static analysis, every command by hand on a
fresh store, and the edge paths a happy-path test never touches.

- `docs/AUDIT-2.md` — what was checked, what was fixed, what was investigated and
  is *not* a bug, and what this audit does not cover
- `harvest/net.py` — a **duplicate `decompress_capped`**, 69 lines of dead code
  that had been shadowed since the refactor extracted `gunzip_members`
- `store/vec0.py` — a `filter` was accepted and ignored; it now raises
- `serve.py` — `GET /tables/<id>` no longer answers 404 for a table that exists
  and is empty
- 49 unused imports removed across 28 files

**The one that looked like a bug and was not:** `entities._surfaces()` counts key
*columns*, not rows. A column with repeats is not a key candidate at all, so the
two counts are always equal — the expensive version was reverted and the
**docstring** was fixed instead. The audit records the reasoning, because a
finding that turns out to be wrong is still a finding.

---

## Wave 30 — the agent fabric, designed · PR #26

The plan for four levels of agent, sixty-four at a time, with one shared board.
Documentation only: this wave builds nothing, on purpose.

- `docs/AGENTS.md` — the architecture, the context model, the concurrency limits,
  the security model, the transport, the skill pipeline, and **ten steps each
  with a test that fails today**
- `docs/PROGRAM.md`, `README.md` — pointers

**The argument that shaped it:** sixty-four agents each holding a context window
is 34 GB (1B model, 8k) to 275 GB (7B model, 8k) of KV cache. The arithmetic is
in the doc, because it is the reason the context window cannot be the unit of
parallelism. What parallelises is the work; shared state belongs in an
append-only blackboard and the store, read by delta.

**The risk that shaped it:** "one agent can read another's context" is also "one
agent can be instructed by another's context". Entries are data, never
instructions, and step 10 is an injection test that must pass before the feature
touches the open web.

**What it refuses:** shared literal windows, free-form agent-to-agent chat, any
uncapped fan-out, and claiming speed without measuring it (step 9).

---

## Wave 29 — the JSON API reaches everything · PR #25

The server had `/search` and `/ask`; the extraction layer, the entity groups and
the three tools were CLI-only, so a client in another language could not reach
them at all.

- `vrusha/serve.py` — `GET /` (a route index), `/tables`, `/tables/<id>`,
  `/entities`, `/tools`, and `POST /math`
- `docs/SERVE.md` — every route, with `curl` examples, and what is deliberately
  not a route
- `tests/test_serve.py` — 9 tests against a real threaded server

**What is not a route:** `research` fetches the web. A client that holds a socket
open for a page fetch, an extraction and an answer has been given a timeout
problem, not a feature — so it stays a command, and `GET /` says so under
`not_here` rather than leaving it to be guessed.

**On the JS/TS question:** there is no JavaScript or TypeScript in this
repository — zero files. The server is `http.server` from the standard library;
the API is JSON so a client in any language can use it.

---

## Wave 28 — the N+1 in `retrieve()` · PR #24

With the address ranking fixed, the next question was what the rest of the query
cost. The answer was not an algorithm.

- `bench/retrieve_stages.py` — per-stage timing of one query
- `vrusha/store/query.py` — hydration is **one** `SELECT … WHERE chunk_id IN
  (…)` instead of one per candidate
- `tests/test_address_ranking.py` — a regression test that counts the hydration
  statements, because the failure mode is a silent slowdown with correct answers

**The profile:** 5,120 `execute` and 4,960 `fetchone` calls over 80 queries — 62
of each per query, all of them hydrating candidates one row at a time.

| | before | after |
|---|---|---|
| p50 | 11.76 ms | **5.88 ms** |
| p95 | 28.42 ms | **6.73 ms** |
| executes per query | 64 | **5** |

The p95 is the one that matters for a server: **4× better tail**, because the
tail was the sum of sixty round trips.

**What is left:** bm25 (1.94 ms) and the vector read (1.67 ms) — both constant
factors of reading 60 rows from SQLite, not algorithm problems.

---

## Wave 27 — branch cleanup, and the plan salvaged · PR #23

24 branches, all of them debris from squash merges: a squash merge does not
record the branch as merged, so every `wave-*` branch stayed behind.

- every branch verified before deletion: 22 were **0 commits ahead** of main, and
  the other two (`wave-8-extract-design`, `wave-10-formulas`) held only commits
  that had already landed as waves 9 and 10 plus the closed design doc
- the one thing main had lost — the design doc's **build order**, and with it the
  reason tier-2 scripts came last — is now a section in `docs/EXTRACT.md`
- all 24 branches deleted, with their commit SHAs recorded in
  `branches-removed.txt` so anything can be restored

**The lesson:** a squash-merge workflow needs a branch-deletion step, or the
branch list becomes a history nobody reads. The two branches that looked
unmerged were not work in progress; they were the same work under a different
name.

---

## Wave 26 — entity resolution · PR #22

The gap `relations.py` named in its own docstring: exact joins, no fuzzy matching.

- `vrusha/store/entities.py` — normalise, expand abbreviations, block on three
  characters, score with `ratio`, merge with union-find, keep groups of two or
  more; `x_entities` / `x_entity_aliases` store every alias with its score
- `vrusha entities --build`, `vrusha entities`
- `tests/test_entities.py` — 16 tests, half of them refusals

**The measurement that chose the metric:** `token_set_ratio` scores
`acme corp` against `acme corporation` at **72** and against `acme` at **100** —
backwards for this job. Abbreviation expansion plus a character ratio gives 100
and 40 respectively. `rapidfuzz` when installed, `difflib` when not.

**Deliberately conservative:** `Acme` and `Acme Corporation` are not merged. They
are often different companies.

---

## Wave 25 — model2vec is the default embedder · PR #21

On the evidence from waves 21 and 24: `bm25 + hash` scored **8% recall@1**, worse
than BM25 alone, and hash is a feature hash rather than a meaning.

- `config` — `store.embed_backend: model2vec` (falls back to `hash` with a
  printed reason when the package is absent)
- `store/db.py` — `set_embedder()` / `embedder_name()`: the store records which
  embedder produced its vectors and **refuses a second one**, because `hash` and
  `potion-base-8M` are both 256 dimensions and the width check cannot see the
  difference
- `ingest` — the guard runs before a single row is written
- `query` — a mismatch between the recorded embedder and the one in use is a
  warning in the retrieval notes, not a silent wrong answer

**The hazard this closes:** switching embedders at the same width mixes two
vector spaces in one store, and every cosine between them is noise that looks
like a score.

**One test was rewritten, not deleted:** `test_the_default_backend_is_still_hash`
asserted the old default. It now asserts the fallback, which is the property that
had to survive the change.

---

## Wave 24 — the index on real embeddings · PR #20

Every recall table so far came from synthetic vectors. This measures the index on
**2,373 chunks of this repository's own text**, embedded with model2vec.

- `bench/real_corpus.py` — walks the repo, chunks at 500 characters, embeds,
  builds the index, measures recall@10 against the full scan, and reports the
  geometry that explains the result
- `docs/BENCHMARKS.md` — a new section

**The geometry is the finding.** Mean cosine between two random chunks **0.462**,
mean nearest-neighbour **0.816**: real embeddings are clustered, which is the
condition routing needs. At the default `nprobe` (2 of 19 cells) that corpus gets
**79% recall@10 in 0.21 ms**; eight cells reach **98% in 0.41 ms**; sixteen reach
100%.

**What that says about the old tables:** the synthetic isotropic row (12% recall
at low `nprobe`) is a stress test, not a description of any real corpus. A corpus
of text embedded by a real model is clustered; the pathological case has to be
built on purpose.

**Two harness bugs:** the shim lacked `embed_dim()`; and the "rows scored" column
reported the top-10 *returned* rather than the rows actually scanned, which made
every row read "10".

---

## Wave 23 — sqlite-vec as a third backend · PR #19

- `vrusha/store/vec0.py` — `Vec0Backend`: a `vec0` virtual table in
  `<store>.vec0.db`, built from the store's vectors, cosine via
  `distance_metric=cosine`, connection cached (the extension load is ~27 ms)
- `build_backends()` and the router offer it once it exists; `vrusha index build
  --engine sqlite-vec`; `vrusha backends` reports it
- `tests/test_vec0.py` — 6 tests, the important one being **identical top-three
  and scores against the exact scan**

**Measured:** 213 chunks at 256 dims, 0.98 ms per warm query, results equal to
the scan to six decimal places.

**A bug:** the router call used `self.usable(...)` for a helper that is a local
function inside `plan()`, so the router raised and every query fell back to the
store. The fallback is what made that a missing optimisation rather than an
outage — which is the point of having one.

---

## Wave 22 — the address ranking, 142× faster · PR #18

The largest remaining cost in the retriever, fixed.

- `vrusha/store/query.py` — `_address_ranking` scores a cached integer matrix in
  numpy instead of parsing every address and calling `semantic_similarity` per
  chunk; same rule, same order, verified against the function it replaced
- `vrusha/store/db.py` — `chunk_revision()`, a `meta` counter maintained by
  **SQLite triggers** on `chunks`, so every write path invalidates the cache
  including the raw-SQL ones in `passes.py`
- `bench/address.py` — before and after, on the same store
- `tests/test_address_ranking.py` — 10 tests, the important one being equality
  with the old implementation

**20,000 addressed chunks: 349.2 ms → 2.46 ms (142×), identical results.**

Two avoidable causes: `Address.parse` ran 20,000 times per query to recover five
numbers that were already columns, and a per-chunk Python function that
vectorises completely. Wave 7's lesson applied in advance: the freshness check is
a trigger-maintained counter, not a `COUNT(*)`.

---

## Wave 21 — does the vector mean anything? · PR #17

Re-ran retrieval quality with the real embedder, on labelled queries, and the
answer changes the conclusion of wave 20.

- `bench/embedders.py` — 15 documents from `docs/`, 12 hand-written queries that
  avoid the target's own words; bm25 vs hash vs model2vec, at document and at
  chunk granularity, plus RRF fusion as `retrieve()` runs it
- `docs/BENCHMARKS.md` — a new section with all three tables and the caveats

**The numbers.** One vector per document: bm25 17% recall@1, hash 17%,
**model2vec 33%**. Chunked at 500 characters: bm25 **25%**, hash 17%,
model2vec 17%, and fused — bm25+hash 8%, **bm25+model2vec 25% recall@1 / 75%
recall@3 / MRR 0.524**.

**Three findings.** At chunk level keywords win alone, because a 500-character
chunk still contains the words. Fusion is where semantics pays: +17 points of
recall@3 over BM25 alone. And **the old default was actively harmful** — bm25+hash
scores 8% recall@1, worse than BM25 alone, because a lexical hash is a second
copy of the same evidence and RRF double-counts it.

**Bugs in the harness, before the numbers could be trusted:** FTS5 reads a bare
multi-word query as AND, so the first BM25 baseline scored 0% and was a broken
comparison rather than a weak one; and a per-query record keyed by a list raised
`TypeError`.

---

## Wave 20 — survey, and model2vec adopted · PR #16

The AI repo survey, and the one candidate that fixes the biggest gap in the store.

- `docs/SURVEY.md` — six candidates judged on licence, what they add, dependency
  weight and offline testability; a rejected list with reasons (GPL, frameworks,
  torch-heavy PDF stacks)
- `vrusha/store/embed.py` — `Model2VecEmbedder`: static embeddings, ~8 MB, numpy
  inference; dimension read **from the model**, vectors normalised, loaded
  lazily, falls back to `hash` with a printed reason
- `vrusha/config.py` — `store.embed_backend: model2vec`, `embed_model_static`

**Why:** the default embedder was `hash`, so the vector signal could never beat
BM25 — the largest quality gap in the project, visible in retrieval quality, in
the benchmark's isotropic row, and in the advice in `docs/ANN.md`.

**Tested:** dimension discovery, normalisation, the fallback and the aliases,
against a stub module — the hub is not reachable from CI, and a test that needs a
network fails for the wrong reason.

---

## Wave 19 — tools: dubforge, forge3d, cutforge · PR #15

One interface for the three engines, and an honest inventory.

- `vrusha/tools/__init__.py` — `ExternalTool`: found as `module` | `checkout` |
  `cli`, run as a **subprocess** with a whitelisted action list, JSON parsed when
  the tool emits it, timeouts enforced
- `vrusha/tools/{dubforge,forge3d,cutforge}.py` — three registrations
- `vrusha tools`, `vrusha tools NAME [--action] [--option k=v]`
- `docs/TOOLS.md`

**The decision:** the adapter is in-tree, the code is not. Copying dubforge's 83
files would buy drift and pull `trimesh`/`scipy`/`pillow` into a numpy-only
package; one clone, one command and one honest status line is what "merged" needs
to mean.

**What reading the repos found:** `cutforge` is a *specification* — its README
says so — and its LICENSE is "All rights reserved", which cannot be reconciled
with this repository's MIT. It is registered as `mode: specification` and never
available, so the listing tells the truth instead of promising an engine.

---

## Wave 18 — research loop · PR #14

A question in, an answer out, and every source that made it.

- `vrusha/research.py` — `search` → fetch → gate → `ingest` → `extract` → `ask`,
  returning sources as a first-class output
- a source is a URL, title, **licence**, retrieval time, chunk/table/formula
  counts, and `verified` (`unchanged` / `drifted` / `rewritten` / `gone`)
- `vrusha research "question" [--verify] [--licence MIT]`
- `docs/RESEARCH.md`, and this file

**Bugs:** `Answer.hits` is a count, not a list, so the citation loop threw
`TypeError`; a refused page returned zero sources with no explanation; and a
`harvest.min_words` override had **no effect at all** — the gate reads
`rules["quality.min_words"]`. A config key read by nothing is worse than no key.

---

## Wave 17 — math layer · PR #13

Formulae are canonicalised before matching, so two spellings of one expression
are one string.

- `vrusha/store/mathquery.py` — unicode → LaTeX (`∫₀¹`, `x²`, `α`), rendering
  commands dropped, brace-aware `\frac`/`\sqrt`, commutative ordering per side of
  `=`, outer parens stripped, symbols and subformulas, `match()` scoring
  exact > subformula > symbols
- `x_equations` beside the tables, written by `extract`, with document, url and licence
- `vrusha math "e^{i\pi}+1=0"`, `vrusha math` to list
- `docs/PROGRAM.md` (the six-part plan), `docs/MATH.md`

**Bugs:** `=` binds looser than `+`, so ordering the whole expression turned
`sin²+cos²=1` into `cos²=1+sin²`; `\frac` with a nested `\sqrt` did not parse and
left `frac` as a symbol matching everything; collapsing `\)+` ate a legitimate
closing paren; `\left(a+b\right)` canonicalised to `(a+b)`, not `a+b`.
`Costs $5 and $10` was read as two formulae.

---

## Wave 16 — tier-2 scripts · PR #12

Real Python against scraped rows, in a namespace, with the limits stated.

- `vrusha/store/sandbox.py` — user+network namespace (verified `ENETUNREACH`),
  `RLIMIT_CPU/AS/NPROC/FSIZE`, cleared environment, process group killed at the
  timeout, result validated (one row per input row, scalar cells, valid names)
- `x_columns.origin` = `script:<sha256 prefix>`, so a computed value is never
  mistaken for a stated one
- `vrusha derive --script PATH --allow-scripts [--timeout] [--isolation]`

**Not enforced, and documented as such:** filesystem read access, a determined
import escape, and any of it on a kernel without user namespaces.

---

## Fix — a date column is a measure, not a key · PR #11

`normalise()` folds separators, so `2026-11-27` reached the measure check as
`2026 11 27`, the ISO pattern stopped matching, and `launch = renewal` joined at
100% overlap. The pattern matches the folded form.

**Found by:** running `vrusha joins` by hand *after* the wave-14 merge. The tests
were green.

---

## Wave 14 — relation inference · PR #10

Which columns across tables are the same entity, stored with the evidence.

- `vrusha/store/relations.py` — key candidates (unique, non-null, short),
  Jaccard overlap across tables, edges in `x_joins` with `shared` and `overlap`
- `vrusha joins --build`, `vrusha joins`, `vrusha joins --table N`

**Bug:** the first run joined `price` to `price` and `uptime` to `uptime` at 100%
— three distinct prices in a three-row table pass a uniqueness test. A
mostly-numeric column is now excluded unless its name says identity.

---

## Wave 13 — harvest --extract · PR #9

The fetched page is read, not re-fetched.

- `Document.html` kept; `html_to_text(extractor=...)`: `simple` (default) |
  `trafilatura` | `auto`
- `records.extract_document()` — tables and JSON-LD from an in-memory page
- `vrusha harvest --extract [--mdy]`

**Decision reversed:** `auto` was the default for an hour; two existing network
tests failed because the stored text changed when trafilatura was installed. A
corpus must not change character because an optional package is present.

---

## Wave 12 — PDF tables · PR #8

Positioned text becomes a grid, or nothing. No PDF dependency on either side.

- `vrusha/store/pdf.py` — objects and `FlateDecode` via `zlib`, a content-stream
  tokeniser (`Tj TJ ' " Td TD Tm T* Tf`), rows from shared y **reversed** (a PDF's
  y axis points up), columns from recurring x, bands by vertical gap
- `.pdf` picked up by `extract`; the page text is stored for retrieval
- `examples/fixtures/tables/plans.pdf`, written by hand in the test helper

**Bugs:** every table came out upside down; a cell drawn as two runs more than
the column tolerance apart became two columns (now a stated limit).

---

## Wave 11 — JSON-LD · PR #7

Read the schema the page already publishes.

- `vrusha/store/jsonld.py` — grouped by `@type`, nested objects flattened
  (`offers.price` → `offers_price`), a single nested entity is a column not a
  table, several are a table, a broken block is skipped
- `x_tables.kind` (`jsonld` | `html-table`), added in place by a migration
- re-extraction is idempotent

**Bugs:** single nested entities produced one-row tables (`Brand`, `Offer`);
`extract` duplicated records on a second run; the single-file summary had a
different shape from the directory summary.

---

## Wave 10 — formulas · PR #6

Derived columns from a string that cannot execute anything.

- `vrusha/store/formula.py` — `ast`-walked whitelist: numbers, strings, columns,
  arithmetic, comparisons, `and/or/not`, `if/else`, and `min max round abs sum len
  if_null date_diff`. Attribute access, subscripts, comprehensions, lambdas and
  any other call are refused at compile time
- `vrusha derive --table N --name X --expr "..." [--guard ...]`, or `--spec`
- the whole spec compiles before any write

**Bug:** a function name is itself a `Name` node, so `round(price/3, 2)` was read
as a column called `round` and refused.

---

## Wave 9 — extraction · PR #4

Pages and CSVs become typed rows, and `verify --refetch`.

- `vrusha/store/extract.py` — HTML tables and CSV; header detection, type and
  unit inference; Devanagari/Arabic-Indic/Bengali digits, lakh grouping, scale
  words; `unit: mixed` rather than a sum that would be wrong
- `vrusha/store/records.py` — `x_tables` / `x_columns` / `x_rows`, rows as JSON,
  linked to document, chunk and licence
- `verify --refetch` — unchanged / drifted / rewritten / gone / licence-changed
  by word-shingle overlap, dry run unless `--apply`
- `vrusha extract --dir`, `vrusha tables`, `vrusha tables --id N`

**Bugs:** a single body row counted as "every row identical", rejecting every
two-row table; `%m/%d/%Y` sat in the day-first list.

---

## Wave 7 — benchmarks · PR #2

- `bench/suite.py` — recall@1/10/100 with distribution, p50/p95/p99, QPS, build
  time, index size, peak RSS, concurrency, pipeline stages
- `bench/report.py` → `docs/BENCHMARKS.md`, generated from JSON
- **The bug it found:** `is_stale()` called `vector_fingerprint()`, a `COUNT(*)`
  over 200k rows, **1,814 ms on every query**. Fixed with a revision counter in
  `meta`: native query latency 3,325 ms → 18.5 ms

---

## Wave 6 — dual-backend retrieval · PR #1

- `vrusha/store/ann.py` — segmented IVF over int8 or float32, exact rescore,
  atomic six-file format, staleness fingerprint
- `vrusha/store/backends.py`, `router.py` — exact | native | ragforge behind one
  `search()`; capability → resource → freshness → explore → speed → failover
- `vrusha/store/export.py`, `vrusha/serve.py` — SQLite → RagForge, and one JSON API
- `vrusha index build|stats|drop|export`, `vrusha backends`, `vrusha serve`,
  `vrusha query --explain`

**Bug:** the router only timed the backend that answered, so an unmeasured
backend could never win — it now probes it once.

---

## Waves 1–5 — before this file

`harvest` (search, fetch, robots, charset, gates), `address` (the numeric tree),
`passes` (merge, cluster, centroid, graph), `rules` (ten levels), `languages`
(20 profiles), `store` (SQLite + FTS5 + vectors), `tokenize`/`dataset`/`train`/
`generate` (BPE and TinyLM). The audit in `docs/audit/` records the defects found
in that period and their fixes.

---

## How to read a wave

Each wave is one PR, one squash commit on `main`, and one entry here. A wave that
finds a bug in an earlier wave gets its own entry (see the date fix), because the
fix is a change and the record should not quietly grow a correction.
