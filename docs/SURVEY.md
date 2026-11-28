# The AI repo survey

Six candidates, judged on four things, and one adopted. The rule that decides
most of these: **a repository that is a framework cannot be merged into a
repository that is a store** — it would replace the thing this project exists to
be. So the bar is "adds an algorithm, a format or a model", not "adds a stack".

## The criteria

| # | test | why it is first |
|---|---|---|
| 1 | **licence** | MIT / Apache-2.0 / BSD only. GPL code cannot enter an MIT repository without relicensing it, and a survey that ignores this produces a repo nobody can legally ship. |
| 2 | **what it adds** | an algorithm, a format, or a model. Not another orchestrator. |
| 3 | **dependency weight** | this package's only hard dependency is numpy; anything that pulls torch must be optional and lazy |
| 4 | **offline testability** | its behaviour must be pinnable by a fixture, or CI cannot protect the integration |

Three grades of adoption, in order of preference:

- **adopt the idea** — reimplement in ~200 lines (as with the IVF index)
- **adopt a module** — vendor one file with its licence header
- **adopt as an optional backend** — behind the router or the embedder factory

## The shortlist

| repo | licence | what it adds | weight | grade | decision |
|---|---|---|---|---|---|
| **MinishLab/model2vec** | MIT | static embeddings: a distilled token table, ~8 MB smallest, numpy inference, up to 500× faster on CPU | light (`numpy`, `safetensors`, `tokenizers`, `huggingface-hub`) | **adopt as embed backend** | **done — `store/embed.py`** |
| **asg017/sqlite-vec** | MIT / Apache-2.0 | vector search *inside* SQLite: `vec0` virtual tables, pure C, no deps, float/int8/binary | one `.so` via `pip install sqlite-vec` | adopt as optional vector backend | next |
| **approach0/pya0** | MIT | math substructure matching over prebuilt indexes | heavy (ships indexes) | adopt as optional math backend | after that |
| **rapidfuzz** | MIT | fuzzy string matching for entity resolution — the gap left open in `relations.py` | light (wheels) | adopt the idea, or a module | when entity resolution is asked for |
| **unum-cloud/usearch** | Apache-2.0 | HNSW ANN, fast at 1M+ | binary wheel | candidate for scale past ~1M | when the benchmark says so |
| **trafilatura** | Apache-2.0 | boilerplate removal | light | already adopted, optional | done (wave 13) |

### Why model2vec first

The default embedder in this project is `hash` — a deterministic feature hash.
It is honest, dependency-free, and it is **not semantics**: `car` and `automobile`
share no features, so the vector signal can never beat BM25. That has been the
single largest quality gap in the store since it was written, and it shows up in
three places: retrieval quality, the ANN benchmark's "isotropic" row, and the
advice in `docs/ANN.md`.

model2vec fixes it for the price of an 8 MB file and numpy. The trade is real and
worth stating: the embeddings are **uncontextualised** (a word's vector is the
same in every sentence), which loses on some queries — but it is 500× cheaper
than a transformer on CPU, which is the machine this project targets.

```bash
pip install model2vec
vrusha harvest --query "..." --licence MIT    # vectors now mean something
```

**model2vec is the default since wave 25.** The store records which embedder
produced its vectors and refuses a second one at the same width — `hash` and
`potion-base-8M` are both 256 dimensions, so the width check alone cannot tell
two different vector spaces apart.

## Rejected, with reasons

| repo | why not |
|---|---|
| **MathWebSearch/mws** | **GPL-3.** Merging it would relicense this project. Its idea (substitution-tree indexing of Content MathML) is documented in `docs/MATH.md` and partially adopted. |
| **LlamaIndex, LangChain, Haystack** | frameworks. They would replace the store, the gates and the address tree with their own abstractions, and the value here is exactly those. |
| **docling, unstructured** | excellent PDF layout extraction, and both pull torch. `store/pdf.py` already reads positioned text with `zlib` and no dependencies; the gap they fill (layout models) is worth a separate optional tool, not a core dependency. |
| **GraphRAG / LightRAG** | entity-and-community graphs over a corpus. `passes.py` already builds cluster/centroid/edge structure; adopting a framework here would duplicate it with less control. |
| **any vector DB framework** | RagForge and the native IVF already cover it, and both are ours. |

## What was actually built

`vrusha/store/embed.py` gained `Model2VecEmbedder` and a `model2vec` branch in
`get_embedder`:

- the dimension is read **from the model**, not from the config — a 384-dim model
  with a 256-dim config stores 384-dim vectors, which is the bug this prevents
- vectors are normalised to unit length, so cosine is a dot product everywhere
- loaded **lazily**: `import vrusha` still needs nothing but numpy
- if the module or the hub is missing, it falls back to `hash` and says so —
  a machine with no network still works

The model itself is not downloaded in CI (the hub is not reachable), so the tests
pin the wiring — dimension discovery, normalisation, the fallback, the aliases —
against a stub. That is the honest limit of what can be verified here, and it is
stated rather than papered over.

## The next two, in order

1. **sqlite-vec** — the store is already SQLite, so vector search inside the
   database removes the index files entirely. It is brute-force today (no ANN),
   but in C, and it compresses to int8 and binary. Behind the same router as
   `native` and `ragforge`, it becomes a third backend with no new format.
2. **PyA0** — the one that would make `vrusha math` a real math search engine.
   Optional, because it ships indexes.
