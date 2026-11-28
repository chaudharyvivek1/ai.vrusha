# Remote agents

**Benchmark:** `remote` · **measures:** a remote agent's round trip over HTTP · **run:** 2026-11-28

## The numbers

| metric | value |
|---|---|
| `append_ms` | 6.55 |
| `delta_ms` | 5.81 |
| `entries` | 50 |

## What each number is

- `append_ms`: POST /board round trip, p50 — server allocates `seq`, client waits
- `delta_ms`: GET /board?since=… for new entries only
- in-process for comparison: **1.29 ms** append

**6.5 ms against 1.3 ms** — the HTTP round trip is the price of being on another
machine, and it is 5×, not 50×.

## What this does not measure

Concurrent remote writers from several machines. `seq` is allocated by one server,
so that case needs a real log rather than this server — see `docs/AGENTS.md` §3.
