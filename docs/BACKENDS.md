# Two engines, one store

`vrusha` ships two retrieval engines and a router that picks between them per
query. This document says what each one is, when it wins, how the choice is
made, and what it costs.

- **native** — `vrusha/store/ann.py`. IVF over int8 or float32, in this process,
  vectors read from SQLite. Built by `vrusha index build`.
- **ragforge** — [RagForge](https://github.com/chaudharyvivek1/RagForge), a Node
  process with JSON segments, int8/1-bit codes, a filter language and an HTTP
  API. Reached over HTTP by `vrusha/store/backends.py`.
- **exact** — the full scan. Slow, always right, never stale. The reference and
  the last resort.

The router is `vrusha/store/router.py`. It is a policy with a short memory, not
a model: every decision comes back with a sentence you can check.

```
                 ┌──────────────────────────────┐
   question ───► │  retrieve()                  │
                 │   bm25  ── FTS5 (SQLite)     │
                 │   vector ──► router ─────────┼──► native    (in process)
                 │   address ── the tree        │      ragforge (HTTP)
                 └──────────────┬───────────────┘      exact    (scan)
                                │
                     chunk ids + scores
                                │
                 ┌──────────────▼───────────────┐
                 │ SQLite: text, url, licence,  │  ← always
                 │ address, the citation        │
                 └──────────────────────────────┘
```

**A backend may only return chunk ids and scores.** Text, licences and citations
always come from SQLite afterwards. That is what stops a second engine from
becoming a second source of truth: delete RagForge's data directory and nothing
is lost but the index.

## The decision, in order

Each gate stops the walk when it fires.

| # | gate | question | if it fires |
|---|---|---|---|
| 1 | `mode` | is routing turned off? | exact |
| 2 | `need` | does the query carry a `filter` or `sort`? | ragforge, or exact with filtering done after retrieval |
| 3 | `resource` | is free RAM below `min_free_mb` **and** below what the native index needs? | ragforge — its memory is another process's, capped and restartable |
| 4 | `freshness` | is the native index missing or stale? | ragforge |
| 5 | `explore` | has one candidate never been timed? | that one, once |
| 6 | `speed` | which has the lower EWMA latency? | that one, unless the margin is under `hysteresis` |
| 7 | `failure` | did one just raise? | demote for `cooldown_s`, try the next |

Gate 5 exists because of a real bug in the first version: the router only timed
the backend that answered, so a backend that was never chosen was never timed,
so it could never win. One probe fixes it.

Gate 6 has hysteresis because an index that is 5% ahead is not worth a different
code path, and flapping between two engines hides regressions.

Every served query returns:

```json
{"backend": "native", "reason": "native has been faster lately (2.10 ms vs 7.31 ms EWMA)",
 "gate": "speed", "skipped": ["ragforge: cooling down after a failure"],
 "ms": {"native": 2.14}}
```

`vrusha query --explain` prints it. `GET /backends` returns it for every engine.

## Running both

The second engine is optional and off until you point at it. Nothing tries to
reach a URL that is empty.

```bash
# 1. seed RagForge from the store, using its own CLI and its own format
vrusha index export --to ragforge --dir ./rf-corpus      # batch JSON files
vrusha index export --to ragforge --jsonl ./corpus.jsonl # one line per chunk
node dist/src/cli.js ingest --in corpus.jsonl --out rf-data

# 2. run it
node dist/src/server.js --dir rf-data --port 8080

# 3. tell vrusha about it
export VRUSHA_BACKENDS_RAGFORGE_URL=http://127.0.0.1:8080
vrusha backends                 # health, rows, EWMA, capabilities
vrusha query "..." --explain    # which engine, and why
vrusha serve --port 8000        # one API, both engines behind it
```

`vrusha index export --url http://127.0.0.1:8080` posts the batches to a running
server instead of writing files, which is how you resync after a harvest.

## The unified API

```bash
vrusha serve --port 8000

curl -s localhost:8000/search -d '{"text":"address tree","k":5}' \
     -H 'content-type: application/json'
curl -s localhost:8000/search -d '{"text":"licence","k":5,"filter":{"lang":"en"}}' \
     -H 'content-type: application/json'      # -> served by ragforge
curl -s localhost:8000/backends
curl -s localhost:8000/ask -d '{"question":"what is an address"}' \
     -H 'content-type: application/json'
```

`vrusha serve` holds **one** router for the life of the process, which is the
only place the latency memory and the cooldown state survive between requests. A
one-shot `vrusha query` starts cold and therefore always begins at the default
backend — correct, but it never learns.

## What is faster, honestly

Merging two engines does not make one query faster than the best engine. What it
buys is that the answer is near the best available engine in every state, and
that losing one engine costs latency instead of an error.

The measured numbers, from `docs/ANN.md` and `bench/headtohead.py`:

| situation | engine | measured |
|---|---|---|
| 200k x 128, fits in RAM | native | 2.2 ms, 100% recall@10 |
| the same query, exact scan | exact | 3.5 s |
| 200k x 128, RagForge float32 | ragforge | 5.5 ms, 98% |
| a filtered query | ragforge | the only one that can |
| native index stale after a harvest | ragforge, then rebuild | no downtime |
| RagForge killed mid-flight | native | next query answers |

Live proof that both serve the same store, and that the router survives losing
one, is `live_merge_test.py` in the review bundle: it ingests 144 chunks, seeds
RagForge through its own CLI, asks both engines the same question (10/10 shared
ids), switches to RagForge for a filtered query, then kills the Node process and
shows the next query answered by native.

## CPU boxes and small GPUs

The index is not what makes a CPU box slow. The embedder is.

| step | cost on a 2-vCPU box | what to do |
|---|---|---|
| embedding the query (1024-dim model) | 30–120 ms | use a 384-dim model, or `hash` for a lexical-only fallback |
| embedding the corpus (once) | minutes to hours | it is a batch job; run it with `--batch` and go to lunch |
| native index search | 2–10 ms | nothing — this is the cheap part |
| exact scan | seconds at 200k | `vrusha index build` |
| training `TinyLM` | hours on CPU, minutes on a small GPU | a 20M model is ~₹200–600 of rented GPU, see `docs/MODEL.md` |

Rules of thumb for a CPU-only deployment:

1. **Pick the embedder by dimension.** 384 dims is 2.7x cheaper than 1024 for
   the same corpus, and at 1024 dims float32 stops fitting at 131k chunks.
2. **Let `ann.dtype` stay `auto`.** float32 below `ram_limit_mb` (no conversion
   at query time), int8 above it (4x smaller, one conversion).
3. **Leave RagForge off unless you need filters or binary codes.** A second
   process is another 100–300 MB and another thing to supervise.
4. **On a small GPU**, embed on the GPU and search on the CPU: `VRUSHA_STORE_EMBED_BACKEND=sentence-transformers`
   with a small model, and keep the index in the process.

## Configuration

```json
{
  "backends": {
    "mode": "auto",
    "ragforge": {"url": "", "timeout": 2.0, "token_env": "RAGFORGE_TOKEN"},
    "cooldown_s": 30.0,
    "hysteresis": 0.20,
    "min_free_mb": 256.0,
    "shadow_every": 0
  }
}
```

| key | meaning |
|---|---|
| `mode` | `auto` routes per query; `exact` forces the scan; `off` skips the router entirely (query uses the native index) |
| `ragforge.url` | empty means "not configured": nothing probes it, nothing reports it as broken |
| `cooldown_s` | how long a failed engine stays demoted |
| `hysteresis` | the margin a switch needs, as a fraction. 0.20 = 20% |
| `min_free_mb` | below this, prefer the out-of-process engine |
| `shadow_every` | reserved: probe the runner-up every N queries |

## Failure modes, and what happens

| failure | result |
|---|---|
| RagForge is down | gate 7 demotes it for `cooldown_s`; native answers; `/backends` shows `errors` and `cooling` |
| native index is stale | gate 4 sends the vector signal to RagForge, or the scan if there is none |
| both indexes are gone | exact scan; slower, correct |
| the embedder changed width | both indexes refuse (they check `dim`); retrieval falls back to the scan and says so |
| RagForge returns garbage ids | the id is looked up in SQLite; an unknown id is dropped, not rendered |
| the store is empty | empty result with `backend: none` — not an exception |

## Limits

- **One router per process.** A one-shot CLI query does not learn; `vrusha serve` does.
- **The router cannot know what it has not measured.** First query after a cold start takes the default path.
- **RagForge is not started for you.** `vrusha backends` reports it as unreachable; you start Node.
- **No shadow probing yet** (`shadow_every` is reserved). The router learns only from queries it serves.
- **The two indexes can disagree on recall.** They use different quantisation and routing; identical ids are typical on a small corpus (10/10 in the live test) but not guaranteed at scale.
