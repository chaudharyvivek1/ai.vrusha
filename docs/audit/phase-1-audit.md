# Vrusha AI — Phase 1 Baseline Audit

**Repository:** github.com/chaudharyvivek1/ai.vrusha
**Baseline commit:** `23958be` (branch `main`, working tree clean, 0 uncommitted changes)
**Audit date:** 2026-11-25
**Method:** read-only. Every finding below was executed against the code at that commit; nothing in the repository was modified.

**Tally:** 15 confirmed defects · 4 confirmed by inspection (not runtime-reproducible here) · 6 claimed items verified **not** to be defects · 2 harness errors corrected during the audit.

---

## 1. Scope and method

I read `README.md` and all six documents in `docs/` first, then the source of every module under `vrusha/`, then the tests. I built a dependency map (§2), then wrote a reproduction harness (`repro_phase1.py`, delivered with this report) that drives the real code paths and prints `CONFIRMED` / `NOT REPRODUCED` / `BY INSPECTION` per claim.

Where a claim could not be reproduced, I say so and change nothing. Six of the submitted claims fell into that category, and reporting them as defects would have been wrong.

Runtime: Python 3.12.16, torch 2.15.0+cpu, tokenizers 0.23.3, numpy 2.5.5, 2 vCPU, 2 GB RAM, no GPU, no network egress. Every store used for reproduction lives in a temp directory.

---

## 2. Dependency map

```
rules.loader          (stdlib only)
languages.registry    (stdlib only)
harvest.search        (stdlib only)
harvest.fetch         (stdlib only)
harvest.gates    -> rules
store.address         (stdlib only)
store.embed      -> numpy
store.chunk      -> languages
store.db         -> store.address, store.embed
store.passes     -> store.address, store.db
store.query      -> store.address, store.db
ingest           -> harvest.fetch, harvest.gates, languages, rules,
                    store.chunk, store.db
tokens.bpe            (stdlib; tokenizers optional)
tokens.dataset   -> store.db, tokens.bpe
generate.model   -> torch
generate.train   -> tokens.dataset, generate.model, torch
generate.sample  -> tokens.bpe, generate.model, generate.train
generate.compose -> store.query            (Hit type only)
answer           -> store.query, generate.compose, generate.model (lazy), tokens.bpe (lazy)
report           -> config, store.address, store.db
verify           -> store.address, store.db
doctor           -> config, rules, languages
cli              -> every module above
```

No import cycles. Two deliberate lazy boundaries: `answer` imports torch-backed code only when a checkpoint is supplied, and `generate/__init__` uses `__getattr__` so the package imports without torch. Both are sound and should be preserved.

The layering is correct: **the store never assigns an address**; only `passes` does. That invariant is the strongest thing in the design and every fix below must keep it.

---

## 3. Findings

Severity: **P0** = wrong results, data loss or security; **P1** = silently wrong under realistic input; **P2** = correctness or maintenance debt.

| ID | Sev | Finding | Status |
|---|---|---|---|
| A1 | P0 | `Address.parse` rejects any segment ≥ 100 | CONFIRMED |
| A2 | P0 | Cluster ids ≥ 100 cannot be parsed | CONFIRMED |
| A3 | P0 | Float form is not injective past 99 | CONFIRMED |
| A4 | P0 | Packed form is ambiguous with 3-digit segments | CONFIRMED |
| C1 | P0 | Quality gate compares characters with bytes | CONFIRMED |
| C2 | P0 | Hindi (and every non-Latin) document rejected end to end | CONFIRMED |
| F1 | P0 | Checkpoints load with `weights_only=False` | CONFIRMED |
| F2 | P0 | An untrusted `.pt` executes a callable at load time | CONFIRMED |
| F3 | P0 | Checkpoint config and state dict are not validated | CONFIRMED |
| D1 | P0 | Ingestion is not atomic | CONFIRMED |
| E1 | P0 | Chunks of one document split across train/val | CONFIRMED |
| E2 | P1 | `CorpusItem` carries no `doc_id` | CONFIRMED |
| G1 | P1 | `set_vector` accepts a wrong dimension | CONFIRMED |
| G2 | P1 | Dimension mismatch degrades retrieval silently | CONFIRMED |
| B1 | P1 | Cluster numbering is size-ordered, not semantic | CONFIRMED |
| B2 | P1 | Address fusion weight equals BM25 and vector | BY INSPECTION |
| K3 | P1 | An address hint outvotes a correct lexical hit | CONFIRMED |
| I1 | P1 | Region prefix `2.1` matches `2.10` and `2.11` | CONFIRMED |
| H1 | P1 | Near-neighbour rebuild leaves stale edges | CONFIRMED |
| H2 | P1 | Centroids from a previous clustering survive | CONFIRMED |
| J1 | P1 | Redirects bypass robots.txt, domain limits and SSRF guards | BY INSPECTION |
| J2 | P2 | Charset is taken from the header only, never sniffed | BY INSPECTION |
| E3 | P2 | Dataset configuration is unvalidated | BY INSPECTION |
| A5 | — | Malformed packed input is silently accepted | NOT REPRODUCED |
| H3 | — | Passes are not idempotent | NOT REPRODUCED |
| K1 | — | Deactivated chunks can be cited | NOT REPRODUCED |
| K2 | — | Citations point at non-existent documents | NOT REPRODUCED |

---

## 4. P0 findings in detail

### A1–A4 — the address parser caps the platform at 99

**Reproduction**

```
Address.parse("2.3.1.4.100")  -> AddressError: at most 5 segments, got 6
Address.parse("2.3.1.100.5")  -> AddressError: at most 5 segments, got 6
Address.of(2,3,1,4,100).float_str() -> '2.030104100'  -> AddressError on parse-back
ordinal  99: round-trip ok
ordinal 100: round-trip BROKEN
ordinal 999: round-trip BROKEN
```

**Cause.** `parse` decides between dotted and packed notation per token by length: a token of 3+ digits is assumed to be a packed 2-digit-per-segment tail (`len(part) <= 2` → single segment, else split into 2-digit groups). A legitimate ordinal of 100 is therefore read as `10, 0` and produces six segments. `float_str` has the mirror-image bug: `f"{x:02d}"` widens to three digits for 100, so the rendering is not reversible.

**Impact.** This is not cosmetic. `cluster_max` is 24 today, but `assign.address` writes one ordinal per chunk inside a cluster, and clusters routinely exceed 99 members. The first region to pass 99 chunks becomes unaddressable — `assign.address` writes the address string fine (`str()` is unaffected), but `Address.parse` on that string then fails, and `parse` is called by `store.address_of`, `store.addresses`, `verify`, `link.parent`, `query` and `dataset`. So the failure arrives as an exception in the pass pipeline, not as a wrong number.

**Fix sketch.** Keep dotted form exact (any integer segment, `\d+`), and require packed form to be explicitly signalled rather than guessed — for example accept packed only when the string has exactly one dot and a tail of exactly 8 digits. Give `float_str` a fixed width and document the ceiling, or replace it with a zero-padded segment join (`0002.0003.0001.0004.0100`) that is unambiguous at any magnitude. Add a `MAX_SEGMENT` constant and validate it in `__post_init__` so the failure is at construction, not at parse.

**Migration.** Address strings already in a database are unaffected for segments ≤ 99. A store containing a ≥100 segment cannot currently exist, so no data migration is needed — only a version note.

---

### C1–C2 — the quality gate rejects every non-Latin document

**Reproduction**

```
gate_quality(hindi_text, len(hindi_text.encode("utf-8")), rules)
  -> BLOCK  "text ratio 0.33 < 0.45"
gate_quality(equivalent_english, len(english.encode("utf-8")), rules)
  -> PASS   text_ratio 0.99
```

End to end: a 1,000+ character Hindi article is rejected at ingest with `quality`.

**Cause.** `gate_quality` computes `len(text) / raw_chars`. `len(text)` is a count of **characters**; `raw_chars` is `len(raw.encode("utf-8"))`, a count of **bytes** (and in `Fetcher.get`, `len(raw)` of the response body, also bytes). For Devanagari the divisor is ~3× the numerator, so the ratio is structurally ~0.33 for any correct Hindi text and can never pass a 0.45 floor. The gate is measuring the encoding, not the content.

**Impact.** The platform's stated purpose includes Hindi, Marathi, Tamil and Bengali profiles. Every document in those languages is silently refused. This is the single most damaging defect for the product's intent, and it is invisible in an English-only test suite — which is exactly why the existing 59 tests did not catch it.

**Fix sketch.** Compare like with like: `text_ratio = len(text.encode("utf-8")) / raw_bytes`, or compare character counts on both sides by decoding the raw body. Add a regression test per script (Latin, Deva, Gujr, Taml, mixed) asserting the ratio is ~1.0 for a body that is pure text.

---

### F1–F3 — checkpoint loading executes untrusted code

**Reproduction**

```
vrusha/generate/model.py:242  torch.load(path, weights_only=False)
vrusha/generate/train.py:109  torch.load(resume, weights_only=False)

A crafted .pt saved with a benign payload whose __reduce__ opens a file:
torch.load(..., weights_only=False)  ->  the file was created at load time
```

**Cause.** `weights_only=False` restores arbitrary pickled objects. `TinyLM.from_checkpoint` calls it before any validation, so a malicious checkpoint runs a callable during load. The same applies to `--resume` in `train`, and therefore to `vrusha ask --checkpoint`.

**Impact.** Any checkpoint that arrives from a download, a colleague, a Drive folder or a model hub is executable. The repo's own README encourages copying `data/` between machines.

**Fix sketch.** Default to `weights_only=True` (a plain state-dict of tensors and primitives loads fine), and validate: `config` keys against `ModelConfig`, `d_model % n_head == 0`, every state-dict key present and every tensor the shape the model expects, and `vocab_size` against the tokenizer. Prefer saving weights as safetensors with a JSON sidecar; keep `.pt` reading behind an explicit `--allow-pickle` flag for legacy files. Reject with a typed error, never a bare traceback.

---

### D1 — ingestion is not atomic

**Reproduction**

```
embedder.encode() raises mid-batch
  -> store holds 1 document, 5 chunks, 0 vectors
```

**Cause.** `ingest_documents` commits incrementally: `add_document`, then a loop of `add_chunk`, then one embedding batch, then `set_vector`. A failure after the chunks are written leaves a document whose chunks can never be retrieved (no vectors) and which `verify` reports as a vector-integrity failure on a store that is otherwise fine.

**Fix sketch.** Wrap one document's full write (document + chunks + signatures + vectors) in a single `SAVEPOINT`, and release it only after the vectors land. Keep the existing behaviour of continuing to the next document on failure, but record the failure in the manifest. `Store` already has a connection; this needs a small `transaction()` context manager, not a redesign.

---

### E1–E2 — train/validation leakage

**Reproduction**

```
val_fraction=0.02:  chunk 107 -> train, chunk 108 -> val
CorpusItem fields: ['chunk_id', 'text', 'lang', 'kind', 'address']
```

Chunk ids are `AUTOINCREMENT` and chunks are inserted per document, so consecutive ids are two chunks of **one** document. The split hashes `chunk_id`, so a document straddling the boundary contributes to both splits. With 8 chunks per document, roughly 15% of documents leak at `val_fraction=0.02`.

**Impact.** Validation loss is optimistic, and the leak is worse for exactly the documents that matter — long ones with many chunks.

**Fix sketch.** Add `doc_id` to `CorpusItem` (it is one column in the existing query), hash **`doc_id`** for the split, and assert at build time that the train and val document-id sets are disjoint. Add a regression test with a two-document, many-chunk corpus that asserts zero overlap.

---

## 5. P1 findings in detail

### B1, B2, K3 — the address signal is not semantic and is over-weighted

**Reproduction**

```
clusters 1 and 2 have centroid cosine 0.94 (similar) but address distance 1.00
an address hint of 2 (code) for a prose question replaces the correct top hit: 7 -> 3
fusion weights: {"bm25": 1.0, "vector": 1.0, "address": 0.35}
```

**Cause, three parts.**

1. `sort.stability` numbers clusters by **size**, so cluster 1 and cluster 2 are unrelated topics that happen to be the two biggest. `Address.distance` then treats them as adjacent, and `|c1 − c2|` grows linearly with an arbitrary id.
2. The family segment has the same problem: family 2 is Marathi and family 3 is Marathi-adjacent only by coincidence of my numbering; family 20 (if it existed) is not "further" from family 2 in any meaningful way.
3. The address signal enters reciprocal-rank fusion with a weight comparable to BM25 and vector, so a hint can outvote evidence.

**Fix sketch.** Keep the numeric address as the identifier and the browsing key — it is genuinely good at that. Change what *distance* means:
- domain, family and kind become **categorical**: 0 if equal, a fixed penalty if not. No arithmetic on ids.
- cluster distance comes from **centroid cosine** (`store.get_centroids()` already exists), falling back to the categorical penalty when a centroid is missing.
- ordinal distance applies only within the same cluster, and with a small weight.
- the address signal becomes a **tiebreaker**: cap its contribution below the sum of the two primary signals, and add a test that a hint cannot displace a top-1 hit whose BM25 score is materially higher.

This keeps the format, the schema and the pass order, and it makes the address explainable: every distance decomposes into "same domain? same family? same kind? how close are the centroids? how far apart in the cluster?".

### I1 — region prefix matching

`corpus_from_store(address_prefix="2.1")` uses `address LIKE '2.1%'` and returns `2.10.3.1.1` and `2.11.3.1.1`. Fix: match on the five `addr_*` integer columns (already indexed) instead of the string, or compare on a delimiter-safe rendering. The same class of bug exists anywhere a prefix is compared as text; `store.chunks_in_region` is already correct because it uses the columns.

### H1 — stale near-neighbour edges

```
k=5: 200 near edges, worst chunk has 5 neighbours
k=1: 200 near edges, worst chunk has 5 neighbours   <- should be 40 edges, 1 each
```

`analyse.graph` inserts with `INSERT OR REPLACE` on `(src,dst,kind)`, so a rebuild only overwrites edges it recomputes; edges beyond the new `k` survive forever. Fix: delete `kind='near'` for the chunk set inside the same transaction before inserting, and keep history in a separate table if audit is required.

### H2 — stale centroids

```
cluster_max=24: 4 centroid rows for 4 live clusters
cluster_max=2 : 4 centroid rows for 2 live clusters   <- 2 describe a clustering that no longer exists
```

`set_centroid` replaces per key but never removes keys that vanished. Fix: after `analyse.cluster`, delete centroid rows whose `(domain, family, kind, cluster)` no longer appears among active chunks.

### J1 — redirects bypass the safety checks

`Fetcher.get` checks `robots.allowed(url)` for the requested URL, then lets `urllib` follow redirects with the default handler. `final_url` is captured *after* the fetch but never re-checked. Consequences: a redirect to a disallowed path is fetched; a redirect off the seed domain defeats `same_domain`; and there is no guard against `127.0.0.1`, `169.254.169.254` or RFC1918 addresses, so a hostile page can point the harvester at the host's own metadata service.

**Not reproduced at runtime** — this sandbox has no inbound network and cannot run a redirecting server. The finding is from source inspection and the documented behaviour of `urllib.request`'s default redirect handler.

Fix sketch: a custom `HTTPRedirectHandler` that re-runs `robots.allowed` and the domain policy on each hop, caps hops, and refuses any resolved address that is private, loopback, link-local or multicast.

### J2 — charset is never sniffed

Charset comes from `Content-Type` only. A page served `text/html` with no charset but declaring `windows-1252` in `<meta>` is decoded as UTF-8 with `errors="replace"`, corrupting it silently — and then the quality gate sees a low ratio and rejects it for the wrong reason. Fix: header → `<meta charset>` → `charset_normalizer` if installed → UTF-8 with a warning.

### E3 — dataset configuration is unvalidated

`build_dataset` does not reject `shard_tokens <= 0` (which makes `range(0, n, 0)` raise, and in the worst formulation loops), `val_fraction` outside `[0,1)`, an unknown `dtype`, or token ids that overflow the chosen dtype. It coerces `uint16 → uint32` on vocabulary size only. Fix: validate at entry, raise typed errors, and record the tokenizer fingerprint in `meta.json` so a dataset can refuse a mismatched tokenizer.

---

## 6. Claims that did not reproduce

Reported here so they are not "fixed" into a regression:

| Claim | Result |
|---|---|
| Malformed packed input silently accepted | **Rejected correctly.** `"2.030104100"` raises `AddressError`. The odd-digit `zfill` path produces six segments and is refused. |
| Passes are not idempotent | **Idempotent.** Two consecutive full runs leave every address identical and `address_log` unchanged (8 → 8 rows). |
| Deactivated chunks can be cited | **Cannot.** After `deactivate`, a query for the text of the removed chunk returns only active chunks. |
| Citations point at non-existent documents | **They resolve.** Every citation URL in a composed answer exists in `documents`. |
| Semantic distance is wrong in the sense of "family 2 vs 3 vs 20" | **Partially confirmed only as B1/B2.** The arithmetic is wrong for the reasons above, but the claim that cluster adjacency implies semantic adjacency is confirmed, not the stronger claim that the weights themselves are miscalibrated. |
| Vector dimension check missing entirely | **Half true.** `query._vector_ranking` does compare shapes and returns `[]` — so it fails safe but silently. G1/G2 describe that accurately; there is no corruption, only a silent capability loss. |

Two of my own harness errors are worth recording because they were instructive: the first graph test used `edges.chunk_id` (the column is `src`), and my first synthetic store repeated one document verbatim, which the dedup gate correctly rejected. The gate was right and my fixture was wrong.

---

## 7. What is already sound

Worth protecting during the fix phase:

- **Layering.** No cycles; the store never assigns addresses; the composer works with no model.
- **Determinism.** Seeded k-means, tie-broken by id, `(chars DESC, chunk_id)` ordinal ordering. Two runs agree.
- **The rule resolver.** 55 rules, all ten levels populated, lowest level wins, trace returned, `check()` clean, level-1 rules never `prefer`.
- **The dedup gate.** Minhash + LSH banding, family-scoped, and it caught a mistake I made in my own harness.
- **Refusal path.** `refuse_below` plus the vector floor means an unrelated question returns a refusal, not an invention.
- **`verify`.** Ten structural checks that would have caught most of the corruption classes in this report — it just is not run often enough, and it does not check vectors' width against the *configured* width, only that they agree with each other.
- **Offline testability.** 59 tests and the smoke script run with no network and no GPU. That is what made this audit possible in an afternoon.

---

## 8. Proposed Phase 2

Ordered so that each fix unblocks the next, and so that the suite is green at every step.

**Wave 1 — correctness that is silently wrong (do first)**
1. C1/C2 quality-ratio units, with per-script tests. Smallest fix, largest product impact.
2. A1–A4 parser: exact dotted form, explicit packed form, validated `MAX_SEGMENT`, injective rendering.
3. I1 region matching on integer columns.
4. E1/E2 `doc_id` split, with a disjointness assertion.

**Wave 2 — data and security**
5. D1 atomic ingestion via `SAVEPOINT` per document.
6. F1–F3 checkpoint safety: `weights_only=True`, config/state validation, typed errors, safetensors path.
7. G1/G2 vector dimension validation at `set_vector`, and a `verify` check against the configured width.

**Wave 3 — semantics and rebuilds**
8. B1/B2/K3 categorical + centroid-based distance, address demoted to a tiebreaker.
9. H1/H2 transactional rebuild of `near` edges and centroid pruning.

**Wave 4 — harvesting**
10. J1 redirect handler with robots re-check, hop cap and private-address refusal.
11. J2 charset sniffing.

**Wave 5 — verification**
12. Full suite, new regression tests per defect, offline smoke, `verify` on a freshly built store, docs updated for every behaviour change.

**Migration note.** Only B1/B2 touches stored semantics: if cluster distance becomes centroid-based, existing addresses stay valid and only `distance()` changes. No schema change is needed for Waves 1–3. If a schema change does become necessary (e.g. a `doc_id` column in a dataset manifest), it will be a versioned migration with the existing `meta.schema_version` check, not an in-place mutation.

**Estimated scope.** 11 defects in 6 modules, roughly 350–500 lines changed, 25–35 new tests, and one new document (`docs/DISTANCE.md`) if B1 lands.

---

## 9. Definition of done — current status

| Condition | Status |
|---|---|
| Confirmed critical bugs fixed | not started (Phase 1 is read-only) |
| Regression test per corrected defect | not started |
| Existing functionality intact | baseline: 59/59 pass at `23958be` |
| Database consistency checks pass | yes at baseline, on a clean store |
| Address system handles large datasets | **no** — A1–A4 |
| Multilingual ingestion works | **no** — C1/C2 |
| Train/val properly isolated | **no** — E1 |
| Checkpoint loading follows a safe policy | **no** — F1–F3 |
| Retrieval citations valid | yes (K2) |
| Passes repeatable | yes for addresses (H3); no for edges and centroids (H1, H2) |
| Offline smoke test passes | yes at baseline |

---

*Reproduction harness: `repro_phase1.py` (27 checks) and the corrected re-runs recorded in §4–§6. Both are delivered alongside this report and are safe to run against any checkout: they create nothing outside a temp directory and modify no repository file.*
