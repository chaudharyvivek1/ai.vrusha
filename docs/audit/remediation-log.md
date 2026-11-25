# Remediation log

Every confirmed defect from the [Phase 1 audit](phase-1-audit.md), its status,
what changed, and what an existing store has to do about it.

Baseline: `23958be`. Reproduce anything here with `python docs/audit/repro_phase1.py`.

| ID | Defect | Status | Wave |
|---|---|---|---|
| A1–A4 | parser capped at 99 | **fixed** | 1 |
| C1–C2 | Unicode documents refused | **fixed** | 1 |
| I1 | region prefix `2.1` matched `2.10` | **fixed** | 1 |
| E1–E2 | train/val leakage | **fixed** | 1 |
| D1 | ingestion not atomic | **fixed** | 2 |
| F1–F3 | unsafe checkpoint loading | **fixed** | 2 |
| G1–G2 | vector dimension unvalidated | **fixed** | 2 |
| H1 | stale near-neighbour edges | **fixed** | 3 |
| H2 | stale centroids | **fixed** | 3 |
| B1/B2/K3 | address distance not semantic; signal over-weighted | **fixed** | 3 |
| J1 | redirects bypass robots/domain/SSRF checks | **open** | 4 |
| J2 | charset never sniffed | **open** | 4 |
| E3 | dataset config unvalidated | **open** | 4 |

---

## Wave 1 — correctness that was silently wrong

### A1–A4 · the parser capped the platform at 99

**Was.** `parse` decided between dotted and packed notation by token length: a
segment of three or more digits was assumed to be a packed tail, so
`2.3.1.4.100` became six segments and raised. `assign.address` writes one
ordinal per chunk inside a cluster, so the first region past 99 chunks made the
pass pipeline throw.

**Now.** A dotted segment is read literally. Packed form is recognised by tail
length alone — 8 digits is four 2-digit groups, 12 digits is four 3-digit
groups — and any other long tail is a malformed packed address, refused rather
than reinterpreted. `float_str` widens to three digits per segment when any
segment exceeds 99, so the rendering stays injective. `MAX_SEGMENT = 999` caps
every segment, which is what makes the two forms impossible to confuse: a
literal segment can never be more than three digits.

**Migration.** None. Every address that could exist before renders byte for
byte as it did (`2.03010417` is unchanged); addresses past 99 simply become
possible.

### C1–C2 · every non-Latin document was refused

**Was.** `gate_quality` divided a **character** count by a **byte** count.
Devanagari is three bytes per character, so correct Hindi scored ~0.33 against
a 0.45 floor and was rejected. Hindi, Marathi, Tamil and Bengali could not
enter the store at all.

**Now.** Both sides are UTF-8 bytes. The gate still rejects a link farm, which
is what it is for.

**Migration.** Documents rejected under the old rule were never stored; re-run
the harvest and they will pass.

### I1 · region prefixes matched their neighbours

**Was.** `corpus_from_store(address_prefix="2.1")` used `address LIKE '2.1%'`
and returned `2.10` and `2.11`.

**Now.** Matched on the integer address columns, so a region contains exactly
its descendants.

### E1–E2 · the validation set leaked into training

**Was.** The split hashed `chunk_id`, and chunks of one document are inserted
consecutively, so a document could sit on both sides of the boundary.

**Now.** `CorpusItem` carries `doc_id`; the split is keyed on it; `meta.json`
records `leaked_documents` and it is zero.

**Migration.** Rebuild datasets. An existing shard was split on the old key.

---

## Wave 2 — data integrity and checkpoint security

### D1 · ingestion was not atomic

**Was.** A document row, its chunks and its vectors were committed
independently. A failure during embedding left a document whose chunks could
never be retrieved and which made `verify` fail on an otherwise healthy store.

**Now.** One document's row, chunks, signatures and vectors are written inside
a `SAVEPOINT` (`Store.transaction()`). A failure rolls that document back and
the run continues to the next one. A short embedder response is treated as a
failure, not silently zipped short.

**Migration.** Run `vrusha verify` on an existing store; the
`every active chunk has a vector` check finds anything the old code left
half-written.

### F1–F3 · checkpoints executed code at load time

**Was.** `torch.load(..., weights_only=False)` in `from_checkpoint` and in
`--resume`. A `.pt` is a pickle, so any checkpoint from a download or a
colleague ran whatever callable it named, before any validation.

**Now.** `weights_only=True` by default; the config is validated against
`ModelConfig` (unknown keys, missing keys, non-positive dimensions,
`d_model % n_head`, even head dimension); every state-dict key must exist and
every tensor must have the expected shape; `--resume` refuses a checkpoint
trained with a different preset or vocabulary; generation refuses a tokenizer
whose vocabulary size differs. Old files raise `CheckpointError` with the fix
instead of falling back to pickle. `allow_pickle=True` is a Python-API opt-in
and is never automatic.

**Migration.** Checkpoints written by this code load unchanged. If you have a
file from before, re-save it with `TinyLM.save_checkpoint`.

### G1–G2 · vector width was unvalidated

**Was.** `set_vector` accepted any width. Mixing widths did not corrupt
anything, but `_vector_ranking` compares shapes, so the whole vector signal
silently became empty.

**Now.** The store records its embedding width in `meta.embed_dim` on the first
vector and refuses any other width or any non-finite value with
`DimensionError`. `retrieve()` reports a width mismatch in its notes instead of
degrading quietly. `verify` checks the recorded width against the stored
vectors.

**Migration.** To change embedder: `store.clear_vectors()` then re-ingest.
`vrusha verify` names this in the failure detail.

---

## Wave 3 — semantics and rebuilds

### H1 · the graph accumulated edges

**Was.** `analyse.graph` inserted with `INSERT OR REPLACE`, so a rebuild with
a smaller `k` left the old edges behind. After `k=1`, chunks still had five
neighbours.

**Now.** The whole `near` edge set is deleted and rebuilt inside the pass, and
the log reports how many were replaced.

### H2 · centroids survived re-clustering

**Was.** `set_centroid` replaced per key but never removed keys that vanished,
so the table described two different clusterings at once.

**Now.** `analyse.centroid` prunes any centroid whose
`(domain, family, kind, cluster)` is not in the live clustering and reports the
count. `verify` checks it.

`link.parent` is rebuilt the same way.

### B1/B2/K3 · distance was arithmetic, and the address could outvote evidence

**Was.** Three problems with one cause. Cluster ids come from `sort.stability`
(cluster size), so `|1 − 2|` was arithmetic on two unrelated topics. Family and
kind ids had the same flaw. And the address signal entered fusion at 0.35,
comparable to BM25 and vector, so a region hint replaced a correct lexical hit.

**Now.** `semantic_similarity(a, b, centroids)` scores domain, family and kind
**categorically** (a mismatch is a fixed penalty, never a difference) and scores
two different clusters by the **cosine of their centroids**. Fusion runs twice:
`primary` from BM25 and vector, `tiebreak` from the address. Ordering is
`(has primary support, primary score, total)`, so the address can reorder hits
with equal evidence and can never displace one the text matched. Default weight
is 0.15.

**Behaviour change, stated plainly.** `Address.distance()` is unchanged and
still arithmetic — it is what makes a prefix a folder, and sorting, browsing
and `vrusha address --distance` still use it. Retrieval uses
`semantic_similarity` instead. If you were relying on address proximity as a
primary ranking signal, your ordering will change: it is now a tiebreaker.

**Migration.** No schema change. Centroids already stored are used as-is. Run
`vrusha passes` once to rebuild the graph and centroids with the new rules.

---

## Still open

| ID | Defect | Why it is still open |
|---|---|---|
| J1 | redirects bypass robots, domain limits and the private-address guard | needs a loopback test server; no inbound network in the build sandbox |
| J2 | charset taken from the header only | same session as J1 |
| E3 | dataset config unvalidated (`shard_tokens<=0`, bad `val_fraction`, dtype overflow) | low risk, scheduled with Wave 4 |

---

## Test map

| Wave | File | Count |
|---|---|---|
| baseline | `tests/test_core.py`, `test_pipeline.py`, `test_model.py` | 59 |
| 1 | `tests/test_regressions.py` | 38 |
| 2–3 | `tests/test_regressions_wave23.py` | 24 |
| **total** | | **121** |

Each regression test fails against `23958be` for the defect it names.
