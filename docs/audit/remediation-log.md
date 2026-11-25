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
| J1 | redirects bypass robots/domain/SSRF checks | **fixed** | 4 |
| N1 | decompression had no bound | **fixed** | 4 |
| J2 | charset never sniffed | **fixed** | 4 |
| E3 | dataset config unvalidated | **fixed** | 4 |
| N2 | robots.txt fetched before the address check | **fixed** | 4 |
| N3 | a redirect could target `file://` and read local files | **fixed** | 4 |
| V4-01 | URL authority parsed with a regex over the netloc | **fixed** | 5 |
| V4-02 | the deflate fallback was dead code | **fixed** | 5 |
| V4-03 | only the first gzip member was decompressed | **fixed** | 5 |
| V4-04 | tests opened every private address class | **fixed** | 5 |

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

## Wave 4 — network safety, decompression and dataset configuration

### J1 · redirects bypassed every check

**Was.** `urllib` followed redirects itself. robots.txt and the domain
restriction were enforced for the URL you asked for and never for where it
sent you, and nothing checked the destination address at all. A permitted
public page could redirect the harvester to `169.254.169.254` or
`127.0.0.1:6379`.

**Now.** Redirects are followed by hand, one hop at a time. Every hop checks
scheme, permitted host, resolved address and robots.txt, in that order, before
a socket opens. Connections are pinned to the validated address so a second DNS
answer cannot move them, and TLS still verifies the hostname. Loops are
detected by canonical URL, and the hop count is capped.

### N2 · robots.txt was fetched before the address check

**Found while fixing J1**, and a genuine hole in the first attempt at the fix:
`_check_destination` asked robots.txt first, and fetching robots.txt is itself
a request to the host. Checking robots for `169.254.169.254` meant connecting
to `169.254.169.254` to ask whether connecting to it was allowed. The address
check now runs first, and the robots fetch goes through the same pinned opener.

### N3 · a redirect could target `file://`

**Found while fixing J1.** `file` is an allowed scheme because local harvesting
is a first-class path, and the redirect loop accepted any allowed scheme — so a
remote page could redirect the fetcher to `file:///etc/passwd` and it would be
read and stored. `file` is now permitted only for the first hop, never as a
redirect destination.

### N1 · decompression was unbounded

**Was.** `gzip.decompress(raw)` on a body that had already been read into
memory. A 40 KB body expanding to 40 GB was decompressed in full before
anything looked at its size.

**Now.** Two ceilings: the compressed body is capped while reading, and the
decompressed output is capped *during* decompression by giving `zlib` a
`max_length` per call. Malformed, truncated and unsupported encodings raise
`DecompressionError`; deflate is tried as both zlib-wrapped and raw, because
both exist in the wild.

### J2 · encoding came from the header or nowhere

**Was.** `headers.get_content_charset() or "utf-8"`, then
`decode(..., errors="replace")`. A page with no charset header and cp1252 bytes
became U+FFFD everywhere, and the quality gate then rejected it for a reason
that had nothing to do with its content.

**Now.** BOM, then header, then `<meta charset>`, then strict UTF-8, then
`charset_normalizer` if installed, then a flagged fallback. A header that cannot
decode the body is discarded rather than trusted. The encoding, its source and a
confidence are recorded in the harvest manifest and in `Document.to_manifest()`.

### E3 · dataset configuration was unvalidated

**Was.** `shard_tokens=0`, `val_fraction=1.5`, `dtype="int8"` and a vocabulary
larger than the dtype all passed silently; `uint16` was silently upgraded to
`uint32` even when the caller had asked for `uint16` explicitly.

**Now.** `validate_build_args` runs before the output directory is created, so a
refused build leaves nothing on disk. `dtype=None` means auto (uint16 unless the
vocabulary needs uint32); an explicit dtype is honoured strictly and a
contradiction raises. Token ids are checked against the dtype after encoding.

**Migration.** `vrusha dataset` now defaults `--dtype auto` instead of
`uint16`. Anyone who relied on the old silent upgrade gets the same result;
anyone who passed `--dtype uint16` with a large vocabulary now gets an error
instead of a different dtype than they asked for.

---

## Wave 5 — URL authority, compression and test isolation

### V4-01 · the hostname came from a regex over the netloc

**Was.** `host_of` did `urlparse(url).netloc.lower().split(":")[0]`. That is
wrong in four ways at once: it keeps userinfo (`http://allowed.com@evil.com/`
becomes `allowed.com@evil.com`, which is neither host in the URL), it breaks an
IPv6 literal (`http://[::1]:80/` becomes `[`), it keeps the brackets, and it
does no IDNA normalisation. Every comparison against a permitted host was
therefore comparing against a string that is not a host.

**Now.** `canonical_host` uses `urlsplit().hostname` and then refuses what
should never have been accepted: credentials in the URL, a missing host, a
malformed port, a non-ASCII name that is not valid IDNA. The permitted-host
check, the address check and the robots fetch all use that one function, and
the robots URL is rebuilt from it with `format_host` so an IPv6 authority is
bracketed exactly once.

**Found while fixing it:** `format_host` was bracketing a `host:port` authority
into `[host:port]`, which broke every robots fetch that used a non-default
port. The Wave 4 suite caught it immediately.

### V4-02 · the deflate fallback never ran

**Was.**

```python
for candidate in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
    try:
        return _inflate(raw, candidate, limit)
    except zlib.error:
        continue
```

`_inflate` converts `zlib.error` into `DecompressionError`, so `except
zlib.error` could never fire: the second candidate was never tried and raw
deflate — which is what many servers actually send — always failed.

**Now.** Both formats are tried, both errors are kept and reported, and
`ResponseTooLarge` is re-raised immediately instead of being treated as a
format mismatch. A limit that fires is a limit, not a reason to try again.

### V4-03 · only the first gzip member was decompressed

**Was.** `zlib.decompressobj` stops at the end of the first member and leaves
the rest in `unused_data`, which was ignored. A two-member body silently lost
everything after the first.

**Now.** `gunzip_members` walks every member under **one cumulative** output
limit, so a body of many small members cannot exceed the ceiling by adding
them up. Trailing NUL padding is accepted (the format allows it); any other
trailing data, or a corrupt later member, raises.

### V4-04 · tests opened the whole private address class

**Was.** The Wave 4 tests reached their loopback server with
`allow_private=True`, which disables the address guard for *every* private
range. A test that disables the control it is testing proves less than it
looks like it proves.

**Now.** `Fetcher(..., allow_hosts={"127.0.0.1"})` — a name allow-list. Naming
one host does not open `127.0.0.2`, `10.0.0.0/8` or `169.254.169.254`, and that
is asserted directly. The blocked-path tests run against a socket-level spy and
assert the forbidden address received **zero connection attempts**, with a
control test proving the spy fires for a permitted connection. One test greps
the suite itself to keep `allow_private=True` from coming back.

---

## Still open

Nothing from the Phase 1 audit. Three items were found during Wave 4 and fixed
in the same branch (N1, N2, N3). The remaining risks are not defects:

| Risk | Why it is not a defect |
|---|---|
| `file://` is still an allowed scheme for the *first* hop | local harvesting is a supported path and the path comes from the operator, not from a web page |
| No proxy-aware pinning | a proxy would tunnel the connection, so the pinned address applies to the proxy. Correct for this design, wrong behind a mandatory proxy; documented rather than silently changed |
| `charset_normalizer` is optional | without it, an undeclared non-UTF-8 page is decoded as UTF-8 and flagged lossy. The flag is the guarantee; the guess is the bonus |

---

## Test map

| Wave | File | Count |
|---|---|---|
| baseline | `tests/test_core.py`, `test_pipeline.py`, `test_model.py` | 59 |
| 1 | `tests/test_regressions.py` | 38 |
| 2–3 | `tests/test_regressions_wave23.py` | 24 |
| 4 | `tests/test_wave4_network.py` | 52 |
| 4 | `tests/test_wave4_dataset.py` | 38 |
| 5 | `tests/test_wave5_network.py` | 38 |
| **total** | | **249** |

Each regression test fails against `23958be` for the defect it names.
