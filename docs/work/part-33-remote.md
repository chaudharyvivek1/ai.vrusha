# Part 33 — remote agents, and the bug that stopped it

**Wave 57 · `vrusha/remote.py`, `vrusha/serve.py`, `bench/registry.py`**

## The bug, and it was not SQLite

I stopped this part once, with two wrong hypotheses: a second SQLite connection
blocking, then a per-request connection. Both were disproved by measurement — the
board appended in **0.0 s** in-process, on the server's own store.

The real cause, found by logging each stage of `do_POST` to a file:

```
1795864035.929 enter /board
1795864035.929 router /board
1795864035.930 body 2 /board        ← the body was read HERE
1795864035.930 board-branch /board  ← my branch read it AGAIN
```

`do_POST` reads the body once, at the top. My `/board` branch called `self._body()`
a **second time** — and `self.rfile.read(length)` on an already-drained stream
**blocks forever**, waiting for bytes that will never arrive. The handler thread
never writes a response, so the client hangs.

**The fix is one line per route:** use the `body` already read, never re-read it.

| before | after |
|---|---|
| `POST /board` hangs → client timeout | **201** in 6.5 ms |
| 9 remote tests: 75 s of timeouts | **13 tests, 7.2 s** |

## Why the earlier attempts missed it

`faulthandler.dump_traceback_later` showed the client waiting and **no handler
thread** — because the blocked thread had already been through `handle_error`, and
the dump was truncated. The stage log was the tool that actually answered the
question, and it took one run.

**The lesson:** "add a log line at each stage" is not a beginner's technique. Two
architectural hypotheses were wrong; a five-line trace was right.

## What was built

- `vrusha/remote.py` — `RemoteBoard`: `append`, `since`, `claim`, `complete`, `agents`
- `vrusha/serve.py` — `GET/POST /board`, `POST /claim`, `POST /complete`, `GET /agents`
- the server creates **one** board at startup, not one per request

## The benchmark

```
append_ms=6.55  delta_ms=5.81  entries=50
```

A remote append costs **6.5 ms** against 1.3 ms in-process — the HTTP round trip is
the price of being on another machine, and it is 5× rather than 50×.

## What `seq` does not guarantee

`seq` is allocated by **the server**, in one transaction, so it stays dense and
monotonic **while there is one server**. Add a second and `seq` is no longer global:
two servers hand out 1, 2, 3 independently and the log has no single order. That is
why `docs/AGENTS.md` §3 says the log is per-run and append-only — a real log
replaces this server without any agent changing. **One server, one writer, one
order**, and the client never allocates a `seq` itself.

## Regression tests, per the acceptance criteria

| criterion | test |
|---|---|
| `POST /board` answers | 201 with a `seq` |
| `GET /board` returns data | entries with refs |
| invalid payload → 4xx | malformed JSON → 400; bad kind → 400 |
| concurrent requests | 8 parallel appends, 8 distinct `seq`, none hung |
| a request after an error | a 400 does not poison the next request |
| **the body is read once** | two appends in a row get seq 1 and 2 |

## Found while rechecking Parts 1–32

Nothing. `ruff` clean, 742 tests green before the change.

## What is next

**Part 34: the loop over HTTP.** `run_pool` still runs in-process; the next part
drives it with remote workers, which is what the JSON server was always for.

## Checklist for this part

- [x] the exact blocking instruction identified, not guessed
- [x] one-line fix per route, no architecture change
- [x] 13 remote tests pass in 7.2 s
- [x] the 742 existing tests still pass
- [x] a benchmark, so the coverage rule is satisfied
- [ ] the loop over HTTP (Part 34)
