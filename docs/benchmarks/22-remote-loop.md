# The loop over HTTP

**Benchmark:** `remote_loop` · **measures:** the loop in-process against over HTTP · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `local_ms` | 58.08 |
| `remote_ms` | 227.29 |
| `overhead_x` | 3.91 |
| `tasks` | 8 |
| `done` | 8 |

## What each number is

8 tasks, 4 workers, the same worker function in both runs.

**3.9×** is the price of the wire. Three round trips per task (claim, append,
complete) at ~6.5 ms each: `3 x 6.5 = 20 ms` per task, 8 tasks over 4 workers,
plus the local work.

## The number to design against

**If the work per task is under ~20 ms, the wire dominates and a remote agent is a
loss.** Distribution pays when the *worker* is expensive — a model call, a fetch, a
transcription — not when it is cheap. This is the measurement that decides where an
L4 agent should live.

## Method

In-process server on loopback, so this is the *best case*: no network latency, no
TLS, no proxy. A real remote agent pays more.
