# Vrusha

Harvest the web, file it under numeric addresses, train a small model on it.

Vrusha is a self-hosted pipeline with five stages in a line. Every stage writes
one artefact you can inspect, and every stage runs offline so you can test the
whole thing before you point it at a website.

```
   web / files
        |
   [1] harvest  ->  what is permitted, and is it content?      manifests, raw text
        |
   [2] store    ->  what is the smallest unit worth citing?    chunks, vectors
        |
   [3] passes   ->  what is this about, what is it near?       addresses, edges
        |
   [4] tokens   ->  how is this text turned into numbers?      tokenizer, shards
        |
   [5] generate ->  what does the model learn from it?         checkpoints
```

Nothing needs a GPU, an API key or a network connection except the harvest
itself.

---

## Quickstart — 60 seconds, no network

```bash
git clone https://github.com/chaudharyvivek1/ai.vrusha
cd ai.vrusha
pip install numpy            # the only hard dependency

python -m vrusha doctor                              # what this machine can do
python -m vrusha harvest --dir examples/fixtures/pages --licence MIT
python -m vrusha passes                              # assign the addresses
python -m vrusha address                             # look at the tree
python -m vrusha ask "why fuse keyword and vector search by rank"
```

The last command prints an answer with citations and the address of every
source. No model was trained and no API was called: it is retrieval plus an
extractive composer.

Then, if you want the model half:

```bash
pip install tokenizers
pip install torch --index-url https://download.pytorch.org/whl/cpu

python -m vrusha tokenize
python -m vrusha dataset
python -m vrusha train --preset tiny --steps 40
python -m vrusha generate --prompt "Retrieval"
```

Or run the whole thing, including every check, in a throwaway directory:

```bash
bash scripts/smoke_test.sh --with-train
```

---

## The address

Every unit of data gets five numbers. Reading left to right moves from the
general to the specific.

```
   2 . 1 . 3 . 4 . 17
   |   |   |   |   +-- position inside the cluster
   |   |   |   +------ cluster: what it is about
   |   |   +---------- kind: function, class, section, paragraph...
   |   +-------------- family: the language
   +------------------ domain: code, prose, data, rules, talk
```

That address is a JavaScript function in cluster 4. Everything about functions
lives under `2.2.3`; everything about JavaScript lives under `2.2`. A prefix is
a real folder, so the address tree *is* the folder tree.

Distance between two addresses uses weights that halve at each level:

```
d(a,b) = 8·|d1−d1'| + 4·|d2−d2'| + 2·|d3−d3'| + 1·|d4−d4'| + 0.5·|d5−d5'|
```

A difference in the domain costs sixteen times a difference in the position.
Two neighbours in a cluster are 0.5 apart; two things in different domains are
8.0 apart. **The left-hand number carries the big difference, the right-hand
number carries the small one.**

```bash
$ vrusha address --distance 1.1.3.1.1 1.1.3.1.2
  distance   0.50     similarity 0.667     same parent yes

$ vrusha address --distance 1.1.3.1.1 2.1.3.1.1
  distance   8.00     similarity 0.111     same parent no

$ vrusha address 2.1.3                 # browse every Python function
$ vrusha query "..." --address 2       # search code only
$ vrusha tokenize --address 2          # train the tokenizer on code only
```

Full specification, including the float form (`2.03010417`) you can sort in a
spreadsheet: [docs/ADDRESS.md](docs/ADDRESS.md).

---

## The ten levels of rules

Rules conflict, so they are numbered. Level 1 is the strictest and nothing may
override it; level 10 is preference. Lower wins, always.

| level | name | holds |
|---|---|---|
| 1 | inviolable | licence, robots.txt, secrets, personal data, safety |
| 2 | legal | attribution, copyright, terms of use |
| 3 | integrity | provenance, no fabrication, UTC timestamps |
| 4 | language | encoding, normalisation, detection confidence |
| 5 | structure | address scheme, chunk boundaries, schema |
| 6 | quality | minimum length, text ratio, link density |
| 7 | dedup | similarity thresholds, merge policy |
| 8 | retrieval | top-k, fusion weights, refusal threshold |
| 9 | training | split, token budget, sequence length |
| 10 | preference | defaults and style |

55 rules ship, in `vrusha/rules/levels/level-01` … `level-10`. The code asks the
rule set instead of hard-coding a number, and the resolver prints why:

```bash
$ vrusha rules --explain licence.allowed
  winner  level 1  licence.allowed  [allow]
  value   ["MIT", "Apache-2.0", "BSD-3-Clause", ...]
  why     Only these licences permit storing the material and training on it.
  from    levels/level-01/01-licence.json
```

Details and how to add a rule: [docs/RULES.md](docs/RULES.md).

---

## The passes

Twelve deterministic operators reshape the store. Nothing is ever deleted — a
filtered or merged chunk is deactivated and keeps its row, its text and its
address history.

| pass | level | job |
|---|---|---|
| `filter.policy` | 1 | deactivate anything the licence rules refuse |
| `filter.quality` | 6 | deactivate fragments and boilerplate |
| `filter.stale` | 3 | flag material older than the stale window |
| `merge.exact` | 7 | identical text: keep one, link the rest |
| `merge.near_duplicate` | 7 | cosine ≥ 0.97: keep one, link the rest |
| `analyse.cluster` | 5 | k-means per (domain, family, kind) |
| `analyse.centroid` | 5 | the centre of each cluster |
| `sort.stability` | 5 | order clusters by size, biggest first |
| `assign.address` | 5 | write the five-segment address |
| `reflow.address` | 5 | compact ordinals, log every change |
| `analyse.graph` | 8 | top-k nearest neighbours as edges |
| `link.parent` | 5 | every chunk points at its parent region |

```bash
vrusha passes --list
vrusha passes --only assign.address
```

The registry is the extension point: a new pass is a decorated function and one
line in the config. [docs/PASSES.md](docs/PASSES.md).

---

## Commands

| command | what it does |
|---|---|
| `doctor` | probe python, sqlite, FTS5, torch, rules, languages, disk |
| `search "q"` | search the web, store nothing |
| `harvest` | search/fetch/crawl, gate, chunk, embed, store |
| `ingest --dir` | the same for local files, no network |
| `passes` | run the operators that reshape the store |
| `address [region]` | browse the tree, or measure two addresses |
| `query "q"` | retrieval only — the honest view |
| `ask "q"` | retrieve, then answer with citations |
| `rules --explain KEY` | why the system decided something |
| `languages` | the profiles it carries, and detection |
| `tokenize` | train a tokenizer on the store |
| `dataset` | build binary shards |
| `train` | train the model |
| `generate` | sample from a checkpoint |
| `verify` | quality control over the store |
| `report` | one page |

Every command takes `--json`, `--root` and `--config`.

---

## Search backends

`auto` falls down the ladder and never fails because a key is missing.

| backend | needs |
|---|---|
| `tavily` | `TAVILY_API_KEY` |
| `brave` | `BRAVE_API_KEY` |
| `searxng` | `SEARXNG_URL` (your own instance) |
| `ddg` | nothing — the DuckDuckGo HTML endpoint |
| `fixtures` | nothing — a local JSON file of results |
| `store` | a database — search what you already harvested |

The fixtures backend is not a toy: it is how the pipeline is tested and demoed
offline, and how you replay a search that mattered.

---

## Status

**Working, tested.** 59 tests, no network, no GPU.

- addresses: parse, prefix, distance, float form, round-trip
- the ten-level rule resolver, with trace and `--check`
- 20 language profiles; detection across Devanagari, Tamil and Latin scripts
- harvesting: robots.txt (fail-closed), rate limit, HTML → text, local files
- seven gates, each tied to its rule level; minhash near-duplicate detection
- SQLite store with FTS5, vectors, edges, address log
- twelve passes, deterministic, logged, with a full `verify` pass
- retrieval: BM25 + vector + address proximity, fused by rank
- extractive answers with citations, and a real refusal path
- tokenizers: HF byte-level BPE, plus a pure-python fallback
- shards, split by hash of chunk id, uint16
- TinyLM: forward, causal mask, RoPE, generation, training, checkpoints

**Working, but small.** The default embedder is deterministic feature hashing,
not a language model — it has no notion of a synonym. It exists so the pipeline
runs and the tests mean something. Switch to `ollama` or
`sentence-transformers` when you care about recall.

**Not built.**

- The model is not trained on a real corpus. `tiny` on 20k tokens produces
  fluent repetition; that is the honest result of 20k tokens, not a bug.
  [docs/MODEL.md](docs/MODEL.md) has the arithmetic.
- No KV cache, no flash attention, no mixed precision in the trainer.
- No vector index beyond a linear scan. Fine at 10k chunks, not at a million.
- No HTML-to-markdown for code sites, no PDF ingestion, no JS-rendered pages.
- The gates decide what is *permitted*, not what is *good*. Quality is your
  judgement, and the retrieval scores are how you apply it.

---

## Requirements

Python 3.10+ and `numpy`. Everything else is an upgrade:

| package | why | if missing |
|---|---|---|
| `tokenizers` | fast byte-level BPE | a pure-python trainer is used: correct, slow above ~1M chars |
| `torch` | train and generate | harvest, store, passes, query, tokenize and dataset still work |
| `sentence-transformers` | in-process embeddings | the hash backend is used |

```bash
pip install -e .                  # the package and its CLI
pip install -e ".[all]"           # plus tokenizers, torch, sentence-transformers
```

---

## Layout

```
vrusha/
  cli.py          every command; the only entry point
  config.py       every tunable, with the defaults the tests use
  ingest.py       documents -> chunks -> vectors -> database
  answer.py       retrieve, then answer with a model or the composer
  report.py       the one-pager
  verify.py       quality control over the store
  doctor.py       what this machine can do
  harvest/        search, fetch, gates
  store/          address, chunk, embed, db, passes, query
  rules/          the resolver and levels/level-01 .. level-10
  languages/      the registry and 20 profiles
  tokens/         bpe, dataset
  generate/       model, train, compose, sample
docs/             ARCHITECTURE, ADDRESS, RULES, PASSES, OPERATIONS, MODEL
examples/         fixtures: a local "web" the tests harvest
scripts/          gen_rules.py, gen_languages.py, smoke_test.sh
tests/            59 tests
```

---

## Docs

| document | what it answers |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | what each stage owns, and what is deliberately not here |
| [ADDRESS.md](docs/ADDRESS.md) | the address scheme in full |
| [RULES.md](docs/RULES.md) | the ten levels and how to add a rule |
| [PASSES.md](docs/PASSES.md) | each pass, why the order matters, how to add one |
| [OPERATIONS.md](docs/OPERATIONS.md) | the daily loop, what you may harvest, troubleshooting |
| [MODEL.md](docs/MODEL.md) | presets, token budget, measured throughput, limits |

## Licence

MIT. The code is yours. What you harvest is not covered by that, and the gates
are there to remind you.
