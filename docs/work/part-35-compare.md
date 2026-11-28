# Part 35 — four levels against one agent, and Phase 6 closed

**Wave 59 · `vrusha/compare.py`, `bench/registry.py`**

## The question, and the answer

`docs/AGENTS.md` §10 has carried this since the start: **does a hierarchy of agents
beat one agent with a good retrieval layer?** Measured on the same 40 questions,
same embedder, same retrieval:

```
metric                single  orchestrated
------------------------------------------
seconds                 0.32          0.20
recall@5               0.475         0.475
ndcg@10                0.349         0.349

cost ratio: 0.62x    quality delta: +0.000
board entries: 42    tasks: 40
```

**Quality is identical — and it must be, because both call the same `retrieve()`.**
That is the finding, and it is worth stating plainly: for single-shot questions a
hierarchy cannot help, because there is no step 2 that needs step 1's output.

## The trap in the numbers, and why I did not take it

The orchestrated run looks **faster** (0.62×). It is not the hierarchy that did
that — it is the **4-worker pool**, which the single loop does not use.

Strip the hierarchy and put the pool on the single loop, and you get the same speed
with **2 board entries instead of 42**. Reporting "orchestration is 1.6× faster"
would be the most misleading true sentence in this repository, so the work log says
what actually happened.

## What the hierarchy costs, concretely

| per 40 questions | single | orchestrated |
|---|---|---|
| board entries | ~2 | **42** |
| claims | 0 | **40** |
| registry slots | 0 | **up to 8** |
| quality | 0.475 | 0.475 |

## What this cannot show

**The benefit.** This corpus has no multi-step goal — no task where step 2 consumes
step 1's output. That is exactly what orchestration is for, and this measurement
cannot reach it. It can only show the cost, which is why the honest conclusion is
narrow:

> **For single-shot questions, use the pipeline, not the hierarchy.** The hierarchy
> is built, tested and measured; its justification is a multi-step corpus this
> project does not have yet.

## Errors found and fixed

**None.** The comparison is two runs of existing code.

## Found while rechecking Parts 1–34

Nothing. `ruff` clean, 761 tests green.

## Phase 6, closed

| part | result |
|---|---|
| 31 the loop | 7 ms/task overhead; found a run-id collision |
| 32 the pool | 3.71× at 4 workers, flattens at 8 |
| 33 remote agents | the body was read twice; 6.5 ms round trip |
| 34 the loop over HTTP | 3.9× the local cost |
| 35 the comparison | **identical quality, 42 entries, no benefit here** |

## What is next

**Phase 7 — the gaps the review named (Parts 36–38):** JavaScript-rendered pages,
then the multimodal skills, then the external datasets (BEIR/CRAG subsets) that
Phase 0 deferred.

## Checklist for this part

- [x] both runs on the same questions, same retrieval (tested)
- [x] quality delta asserted to be zero, with the reason
- [x] the cost reported honestly — including that the pool, not the hierarchy, was faster
- [x] the board entries and claims counted
- [x] what it cannot show written down
