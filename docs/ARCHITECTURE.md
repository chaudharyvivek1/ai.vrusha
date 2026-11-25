# Architecture

Vrusha is five stages in a line. Each stage reads what the previous one wrote
and writes one artefact. Nothing skips a stage, and every stage can be run
alone and inspected.

```
   web / files
        |
        v
   [1] harvest  ----->  manifests/*.jsonl   (what was seen, and what was refused)
        |                 raw text in the database
        v
   [2] store    ----->  vrusha.db           (documents, chunks, vectors, edges)
        |
        v
   [3] passes   ----->  addresses           (address_log, centroids, edges)
        |
        v
   [4] tokens   ----->  tokenizer.json      (vocabulary)
        |                 dataset/*.bin      (shards of token ids)
        v
   [5] generate ----->  models/*/latest.pt  (weights)
```

## What each stage owns

| stage | reads | writes | the question it answers |
|---|---|---|---|
| harvest | the web, or files | `manifests/`, documents | is this permitted, and is it content? |
| store | documents | chunks, vectors, signatures | what is the smallest unit worth citing? |
| passes | chunks + vectors | addresses, centroids, edges | what is this about, and what is it near? |
| tokens | chunks | tokenizer, shards | how is this text turned into numbers? |
| generate | shards | checkpoints | what does the model learn from it? |

The boundary that matters most is between store and passes. **The store never
assigns an address.** It stores chunks with a null address and a vector.
Passes assign addresses, and only passes change them. That is what makes the
address log complete: every address a chunk has ever had was written by a named
pass at a known time.

## The address is the join key

Everything meets at the address. A chunk has one. A query can name one. An
edge connects two. The tokenizer can be trained on a region of the tree
(`vrusha tokenize --address 2.1`). The report summarises the tree.

That is the design bet: if related material sits at nearby addresses, then
proximity is a retrieval signal, a training-data filter, a browse interface and
a debugging tool, all from one number. See [ADDRESS.md](ADDRESS.md).

## Harvest safety: the network is the attack surface

The harvester follows URLs that strangers wrote, so `harvest/net.py` treats
every one as hostile until it has been checked.

**Redirects are followed by hand.** `urllib` follows a redirect itself and
never looks at where it landed, so robots.txt and the domain restriction apply
only to the URL you asked for. Here `NoRedirectHandler` makes the opener return
the 3xx, and `_get_checked` walks the chain one hop at a time. Every hop passes
`_check_destination` before a socket opens:

1. **scheme** — `http`, `https`, and `file` only for the first hop. A remote
   page must never be able to redirect the harvester into reading a local file.
2. **permitted host** — checked before any DNS lookup, so a refused page's
   hostname is not even resolved.
3. **address** — the hostname is resolved and the *address* is validated.
   Loopback, private, link-local, multicast, reserved and unspecified are
   refused. `169.254.169.254` is the cloud metadata service, and it is the
   reason this check exists.
4. **robots.txt** — fetched through the same pinned opener, so the robots
   request cannot be rebound either.

**Connections are pinned.** `PinnedHTTPConnection` resolves once, validates the
answer, and connects to *that* address. Resolve-then-connect-by-name leaves a
window where the second DNS answer is a different one; connecting to the
validated sockaddr closes it. TLS still verifies the certificate against the
hostname from the URL (`server_hostname`), so pinning does not weaken it.

**Bodies are bounded twice.** The compressed body is capped while reading, and
the decompressed body is capped *during* decompression: `zlib.decompressobj` is
given a `max_length` on every call, so a 40 KB gzip that expands to 40 MB
raises before the 40 MB is allocated. Checking the size afterwards would mean
allocating the bomb first, which is the attack.

**Encoding is decided once and recorded.** BOM, then HTTP header, then
`<meta charset>`, then strict UTF-8, then a guess, then a flagged fallback. The
chosen encoding, its source and a confidence go into the harvest manifest, so a
mis-decode is visible after the fact instead of being silently baked into the
corpus.

## Why SQLite

One file, no server, no daemon, no port. FTS5 gives BM25 inside the same
transaction as the vector BLOBs, so a query never sees a half-written store.
The whole corpus is a file you can copy to a laptop.

The cost is that vector search is a linear scan over the BLOBs. At 10,000
chunks of 256 dimensions that is 10 MB and about 3 ms per query — fine. At a
million chunks it is not, and the answer is `sqlite-vec` or a real vector
index, behind the same `get_vectors` call.

## Why the hash embedder is the default

A deterministic feature-hashing embedder needs nothing, is reproducible across
machines, and makes the tests meaningful. It is not a language model: it has no
notion of a synonym. It exists so that the pipeline is runnable and testable on
day one, and so that when retrieval is wrong you can tell whether the problem
is the embedder or the plumbing.

Switch with `store.embed_backend` in the config: `ollama` for a local
embedding server, `sentence-transformers` for in-process.

## Why the composer exists

`vrusha ask` answers from retrieved text with no model at all. It is
extractive: it picks the sentences that cover the question and cites them. It
will not write prose and it will not generalise.

It exists because a trained model is the last thing you get, not the first. The
store is useful on day one. And when a model is in the loop, the composer is
the baseline that shows whether the model is adding anything.

## What is deliberately not here

- **No crawler that follows everything.** Depth, page count and domain are
  capped, robots.txt is enforced, and the frontier is bounded.
- **No self-training loop.** The model does not harvest, retrain itself or
  improve itself. It is a file you produce by running `vrusha train`. The
  pipeline is supervised, and the report is how you supervise it.
- **No deletion.** A filtered or merged chunk is deactivated and keeps its
  row. Deletion would make the pass log a story you cannot check.
- **No claim of quality.** The gates decide what is *permitted*, *parseable*,
  *unique* and *secret-free*. They do not decide what is *good*. That is your
  judgement, and the retrieval scores are how you apply it.

## File map

```
vrusha/
  cli.py            every command; the only entry point
  config.py         every tunable, with the defaults the tests use
  ingest.py         documents -> chunks -> vectors -> database
  answer.py         retrieve, then answer with a model or the composer
  report.py         the one-pager
  verify.py         quality control over the store
  doctor.py         what this machine can do
  harvest/
    search.py       five backends behind one function
    fetch.py        robots, rate limit, HTML -> text, local files
    gates.py        the seven gates, each tied to a rule level
  store/
    address.py      the five-segment address and its distance
    chunk.py        one unit of meaning per row
    embed.py        hash, ollama, sentence-transformers
    db.py           the SQLite schema and its queries
    passes.py       the operators that reshape the store
    query.py        BM25 + vector + address, fused by rank
  rules/
    loader.py       the ten-level resolver
    levels/         level-01 .. level-10, one folder each
  languages/
    registry.py     profiles, extension mapping, detection
    profiles/       five natural languages, fifteen programming languages
  tokens/
    bpe.py          hf and pure-python byte-level BPE
    dataset.py      shards, split, batching
  generate/
    model.py        TinyLM
    train.py        the loop and the schedule
    compose.py      the extractive answerer
    sample.py       generation
```
