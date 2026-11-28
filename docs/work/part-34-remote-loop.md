# Part 34 — the loop over HTTP

**Wave 58 · `vrusha/orchestrator.py`, `bench/registry.py`**

## What was built

`run_remote()` — the same loop, with every board and claim call going over HTTP. An
L4 agent on another machine claims from the server, works locally, reports back.

```
goal: a then b then c
  3 done, 0 failed of 3 in 0.08s, 5 board entries
  ok   a    did a
  ok   b    did b
  ok   c    did c
```

## The benchmark: what the wire costs

```
local_ms=58.08  remote_ms=227.29  overhead_x=3.91  done=8  tasks=8
```

8 tasks, 4 workers: **58 ms in-process against 227 ms over HTTP — 3.9×**.

That is three round trips per task (claim, append, complete) at ~6.5 ms each, so
the arithmetic matches the measurement: `3 × 6.5 ≈ 20 ms` per task, × 8 tasks ÷ 4
workers ≈ 40 ms, plus the local work.

**3.9× is the price of being on another machine, and it is the number to design
against.** If the work per task is under ~20 ms, the wire dominates and a remote
agent is a loss. The loop is worth distributing when the *worker* is expensive — a
model call, a fetch, a transcription — not when it is cheap.

## Errors found and fixed

**None.** `run_remote` is `run_pool` with the local calls replaced, and the two
behaviours are asserted to match: same completion, same findings, same retry rule,
same pool bound.

## What it will not do

| limitation | why |
|---|---|
| survive a server restart mid-run | a claim's lease expires; the task is retried, the run is not resumed |
| run more than the pool | the cap holds remotely too (tested) |
| work without a server | that is `run_pool`, which needs no network at all |
| hide a dead server | an unreachable server raises, it does not hang (tested) |

## Found while rechecking Parts 1–33

Nothing. `ruff` clean, 755 tests green.

## What is next

**Part 35: the honest evaluation.** Four levels versus one agent with a good
retrieval layer — the question `docs/AGENTS.md` §10 has been carrying since the
start. It is the part that decides whether any of Parts 22–34 earned their place.

## Checklist for this part

- [x] the remote loop completes every task (tested)
- [x] findings and the report reach the server (tested)
- [x] claims are recorded server-side, so a task cannot be taken twice (tested)
- [x] a failure is retried once, then marked (tested)
- [x] the pool bound holds remotely (tested)
- [x] an unreachable server raises instead of hanging (tested)
- [x] benchmarked: 3.9× the local cost, with the arithmetic shown
- [ ] the four-levels-versus-one comparison (Part 35)
