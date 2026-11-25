# Operations

The day-to-day: what to run, in what order, and what to do when a number looks
wrong.

## The loop

```bash
# once
python -m vrusha doctor

# every time you add material
python -m vrusha harvest --query "..." --licence MIT     # or --url / --seed / --dir
python -m vrusha passes
python -m vrusha verify

# when you want to ask it something
python -m vrusha query "..."        # retrieval only — always this first
python -m vrusha ask   "..."

# when the store is big enough to be worth training on
python -m vrusha tokenize
python -m vrusha dataset
python -m vrusha train --preset 6m
python -m vrusha report
```

`query` before `ask`, always. If retrieval returns the wrong passage, the answer
will be wrong and you will blame the model. The model is not the problem.

## Harvesting, and what you may harvest

The gates refuse, by name, sites whose terms forbid crawling or reuse
(`policy.refused_domains` at level 1). robots.txt is checked before every
request and a robots.txt that cannot be read means the host is treated as
disallowed.

Beyond that, the honest position:

- **Your own code and notes are the best first corpus.** You own them, the
  licence question disappears, and it is the material you actually want the
  model to know. `vrusha harvest --dir ~/code/myproject --licence MIT`.
- **Permissively licensed repositories** are the second. Check the licence file
  is detected — `vrusha harvest` records it per document, and
  `vrusha report` shows the distribution.
- **Web pages** are the third and the messiest. Prefer sources with an explicit
  licence; the level-1 rule refuses `unknown` rather than guessing.
- **Nothing** here is legal advice. A gate that passes is a gate that passed,
  not a clearance.

### Politeness

`http.rate_per_host` defaults to one request per second per host, with a byte
cap and a timeout. A crawl is capped by `--max-pages` and `--depth`, and stays
on the seed domain unless you pass `--any-domain`. These are defaults you can
change; the reason they are defaults is that the alternative gets your IP
blocked and the site annoyed.

## Network safety, and the settings that control it

| key | default | what it does |
|---|---|---|
| `http.max_redirects` | 5 | hops followed before the chain is refused |
| `http.max_compressed_bytes` | 3,000,000 | ceiling on the body on the wire |
| `http.max_decompressed_bytes` | 20,000,000 | ceiling on what it may expand to, enforced *during* decompression |
| `http.allow_private` | `false` | loopback and private destinations. **Tests only**; the CLI never sets it |

A fetch can fail in ways you should be able to tell apart:

| error | means |
|---|---|
| `UnsafeDestination: ... is a link-local address` | the URL, or a redirect, pointed at `169.254.x.x` or another internal range |
| `UnsafeDestination: robots: ...` | robots.txt disallows it, or could not be read (fail-closed) |
| `RedirectError: ... leaves the permitted host` | a crawl tried to leave the seed domain |
| `RedirectError: redirect loop` | the chain came back to a URL it had already visited |
| `ResponseTooLarge` | the body, compressed or decompressed, exceeded its ceiling |
| `DecompressionError` | the gzip/deflate stream was malformed, truncated, or an encoding we do not support |

None of these is a reason to raise the limit. A body that expands 1000× is
either a mistake or an attack, and both want the same answer.

If a site you legitimately want is refused, the fix is `policy.refused_domains`
in reverse — check that the URL is public and that robots.txt allows it — not a
config flag that turns the guard off.

## Reading the numbers

| number | where | what it means when it is wrong |
|---|---|---|
| `rejected` | harvest | read `by_reason`. Usually licence or size. |
| `addressed` < `chunks` | report | a pass deactivated something, or `assign.address` did not run |
| `regions` | address | near 1 means clustering found one topic, or vectors are all similar |
| `widest cluster` | report | a third of the store in one cluster means the embedder is too weak to separate it |
| `fertility` | tokenize | above ~4 tokens/word means the tokenizer does not fit the corpus |
| `val loss` | train | flat means the learning rate or the corpus, not the architecture |

`vrusha verify` is the gate on the store itself. If it fails, stop: an address
that disagrees with its columns makes every distance you compute a lie.

## Backup and moving

The store is one file plus the manifests.

```bash
cp data/vrusha.db ~/backup/vrusha-$(date +%F).db
```

WAL mode means you should copy with the database closed, or use
`sqlite3 data/vrusha.db ".backup /path/out.db"`. Everything else
(`data/raw`, `data/manifests`, `data/tokens`, `data/models`) is a directory you
can `rsync`.

To move to another machine: copy `data/`, then `python -m vrusha verify`. The
schema version is checked on open; an old database is refused rather than
half-read.

## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `stored 0, rejected N: empty document` | the fetch returned no content | check the URL; a 403 shows as `HTTP 403` in the reason |
| everything rejected on licence | you did not pass `--licence`, and the source has none | pass the licence you verified, or do not store it |
| `no validation shard` warning | the store is too small for a 2% split | expected under ~100 chunks; grow the corpus |
| retrieval returns nothing | the vector floor dropped everything | lower `query.min_vector_similarity`, or the store genuinely lacks it |
| `checkpoint unusable, composed instead` | the `.pt` was trained with a different vocabulary | retrain, or point `--tokenizer` at the matching file |
| `database schema is version N` | the code and the file disagree | move the file aside and re-ingest |
| `no rules found under` | you are running outside the package | install it, or set `PYTHONPATH` to the repo root |

## Training on a GPU

Nothing in the code changes; only `--device` and the wall clock.

```bash
python -m vrusha dataset
python -m vrusha train --preset 20m --device cuda --steps 20000 --batch-size 32
```

On a rented GPU or a free Colab T4, `20m` at 400M tokens is a few GPU-hours.
On this class of CPU it is not a build, it is a demonstration — see
[MODEL.md](MODEL.md) for the arithmetic and for the throughput measured here.

## When to stop

Three signals that the corpus, not the model, is the next thing to fix:

1. `query` returns nothing relevant for questions you know the answer to.
2. `vrusha report` shows a third of the chunks in one cluster.
3. `verify` passes but `ask` composes from the wrong region.

All three are retrieval or data problems. Raising the model size fixes none of
them.
