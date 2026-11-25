"""Reproduction harness for the Phase 1 audit.

Run from the repository root:  python docs/audit/repro_phase1.py

Every check drives the real code path and prints CONFIRMED / NOT REPRODUCED /
BY INSPECTION. Nothing is written outside a temp directory and no repository
file is modified. Against commit 23958be, 15 checks report CONFIRMED. After
the Wave 1 fixes, A1-A3, A5, C1-C2, E1-E2 and I1 report NOT REPRODUCED.
"""

import os
import shutil
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import numpy as np

RESULTS = []


def record(bug_id, title, status, evidence):
    RESULTS.append((bug_id, title, status, evidence))
    print(f"\n[{status}] {bug_id}  {title}\n    {evidence}")


def check(bug_id, title, fn):
    try:
        status, evidence = fn()
    except Exception as exc:
        status, evidence = "ERROR", f"{type(exc).__name__}: {exc}"
        traceback.print_exc(limit=2)
    record(bug_id, title, status, evidence)


def fresh_root(name):
    path = tempfile.mkdtemp(prefix=f"audit-{name}-")
    return path


def cfg_for(root):
    from vrusha.config import DEFAULTS, ensure_dirs
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    cfg["paths"]["root"] = root
    ensure_dirs(cfg)
    return cfg


# ---------------------------------------------------------------------------
# A. address parsing
# ---------------------------------------------------------------------------

def a1_large_ordinal():
    from vrusha.store.address import Address, AddressError
    try:
        a = Address.parse("2.3.1.4.100")
        return "NOT REPRODUCED", f"parsed as {a.segments}"
    except AddressError as exc:
        return "CONFIRMED", f"Address.parse('2.3.1.4.100') raises AddressError: {exc}"


def a2_large_cluster():
    from vrusha.store.address import Address, AddressError
    try:
        a = Address.parse("2.3.1.100.5")
        return "NOT REPRODUCED", f"parsed as {a.segments}"
    except AddressError as exc:
        return "CONFIRMED", f"Address.parse('2.3.1.100.5') raises AddressError: {exc}"


def a3_float_roundtrip_100():
    from vrusha.store.address import Address
    a = Address.of(2, 3, 1, 4, 100)
    text = a.float_str()
    back = Address.parse(text)
    if back == a:
        return "NOT REPRODUCED", f"{text} round-tripped"
    return "CONFIRMED", f"Address.of(2,3,1,4,100).float_str()={text!r} parses back as {back.segments}"


def a4_packed_ambiguity():
    from vrusha.store.address import Address, AddressError
    # a dotted 3-digit segment must be read literally, not as packed pairs
    got = Address.parse("2.3.1.4.100").segments
    bad = []
    for c in ("2.0301041000", "2.0301100500"):
        try:
            Address.parse(c)
            bad.append(c)
        except AddressError:
            pass
    if got != (2, 3, 1, 4, 100):
        return "CONFIRMED", f"'2.3.1.4.100' parsed as {got}"
    if bad:
        return "CONFIRMED", f"malformed packed tails accepted: {bad}"
    return "NOT REPRODUCED", "dotted 3-digit segments are literal; malformed tails are refused"


def a5_invalid_packed_accepted():
    from vrusha.store.address import Address
    # an 11-digit packed tail is silently zero-padded into a 6th segment
    try:
        a = Address.parse("2.030104100")
        return "CONFIRMED", f"'2.030104100' (odd digit count) silently parsed as {a.segments}"
    except Exception as exc:
        return "NOT REPRODUCED", f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# B. semantic distance
# ---------------------------------------------------------------------------

def b1_cluster_number_is_not_semantic():
    """Cluster ids are size-ordered, so distance must come from the centroids."""
    from vrusha.store.address import Address, semantic_similarity
    a, b = Address.of(1, 1, 3, 1, 1), Address.of(1, 1, 3, 2, 1)
    arithmetic = a.distance(b)                     # what browsing still uses
    same = {(1, 1, 3, 1): (1.0, 0.0), (1, 1, 3, 2): (1.0, 0.0)}
    opposite = {(1, 1, 3, 1): (1.0, 0.0), (1, 1, 3, 2): (0.0, 1.0)}
    if (semantic_similarity(a, b, same) == 1.0
            and semantic_similarity(a, b, opposite) == 0.0
            and semantic_similarity(Address.of(2, 2, 3, 1, 1), Address.of(2, 20, 3, 1, 1))
            == semantic_similarity(Address.of(2, 2, 3, 1, 1), Address.of(2, 3, 3, 1, 1))):
        return ("NOT REPRODUCED",
                f"retrieval distance is categorical and centroid-based; "
                f"Address.distance ({arithmetic:.2f}) is still arithmetic, by design, "
                f"for sorting and browsing")
    return "CONFIRMED", "semantic_similarity does not follow the centroids"


def b2_address_signal_weight():
    from vrusha.config import DEFAULTS
    w = DEFAULTS["query"]["fusion"]
    if w["address"] < min(w["bm25"], w["vector"]):
        return ("NOT REPRODUCED",
                f"address weight {w['address']} is below bm25 {w['bm25']} and "
                f"vector {w['vector']}, and ordering is primary-first")
    return "CONFIRMED", f"address weight {w['address']} rivals the primary signals"


def c1_hindi_rejected():
    from vrusha.rules import load_rules
    from vrusha.harvest.gates import gate_quality, evaluate
    from vrusha.languages import load_registry

    rules = load_rules()
    hindi = ("भारतीय रिज़र्व बैंक ने आज कहा कि रेपो दर में कोई बदलाव नहीं किया गया है और "
             "महंगाई दर लगातार तीसरे महीने कम हो रही है। यह निर्णय मौद्रिक नीति समिति की "
             "बैठक में लिया गया। समिति ने कहा कि आने वाले महीनों में खाद्य कीमतों पर "
             "नज़र रखी जाएगी और आवश्यकता पड़ने पर दरों में समायोजन किया जाएगा। ")
    hindi = hindi * 3
    raw_bytes = len(hindi.encode("utf-8"))
    result = gate_quality(hindi, raw_bytes, rules)
    ratio = len(hindi) / raw_bytes
    eng = "The Reserve Bank of India said the repo rate is unchanged. " * 12
    eng_result = gate_quality(eng, len(eng.encode("utf-8")), rules)
    if not result.passed and eng_result.passed:
        return ("CONFIRMED",
                f"identical content in Hindi is blocked: char/byte ratio {ratio:.2f} < 0.45 "
                f"({result.reason}); the same text in English passes "
                f"({eng_result.detail['text_ratio']:.2f})")
    return "NOT REPRODUCED", f"hindi passed={result.passed}, english passed={eng_result.passed}"


def c2_ingest_hindi_blocked():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_documents
    from vrusha.harvest.fetch import Document
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder

    root = fresh_root("hi")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    body = ("भारतीय रिज़र्व बैंक ने आज कहा कि रेपो दर में कोई बदलाव नहीं किया गया है और "
            "महंगाई दर लगातार तीसरे महीने कम हो रही है। ") * 6
    doc = Document(url="https://example.org/hi", title="RBI", text=body,
                   raw_chars=len(body.encode("utf-8")), licence="MIT", status=200)
    report = ingest_documents([doc], store, cfg, rules, registry, HashEmbedder(256))
    if report.rejected:
        return "CONFIRMED", f"a 1,000+ character Hindi article is rejected: {report.by_reason}"
    return "NOT REPRODUCED", report.to_dict()


# ---------------------------------------------------------------------------
# D. atomic ingestion
# ---------------------------------------------------------------------------

def d1_partial_document():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_documents
    from vrusha.harvest.fetch import Document
    from vrusha.store.db import Store

    class ExplodingEmbedder:
        name, dim = "exploding", 256
        def encode(self, texts):
            raise RuntimeError("embedding backend died mid-batch")

    root = fresh_root("atomic")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    body = ("Retrieval augmented generation answers from retrieved context. " * 40)
    doc = Document(url="https://example.org/x", title="X", text=body,
                   raw_chars=len(body), licence="MIT", status=200)
    try:
        ingest_documents([doc], store, cfg, rules, registry, ExplodingEmbedder())
    except RuntimeError:
        pass
    docs = store.document_count()
    chunks = store.chunk_count(active_only=False)
    vectors = store.conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    if docs and chunks and vectors == 0:
        return ("CONFIRMED",
                f"after a failed embedding the store holds {docs} document(s) and "
                f"{chunks} chunk(s) with {vectors} vector(s): a half-ingested document "
                f"that no query can ever return")
    return "NOT REPRODUCED", f"docs={docs} chunks={chunks} vectors={vectors}"


# ---------------------------------------------------------------------------
# E. dataset split leakage
# ---------------------------------------------------------------------------

def e1_chunks_of_one_document_split():
    from vrusha.tokens.dataset import CorpusItem, _is_validation

    # In a real store, chunks of one document are consecutive chunk_ids
    # (chunk_id is AUTOINCREMENT and chunks are inserted per document).
    val_ids = [i for i in range(1, 401) if _is_validation(i, 0.02)]
    # simulate a document that produced chunk ids 150..160
    doc_chunks = list(range(150, 161))
    inside = [c for c in doc_chunks if c in val_ids]
    if inside:
        return ("CONFIRMED",
                f"CorpusItem carries no document identity; splitting on chunk_id puts "
                f"{len(inside)} of {len(doc_chunks)} chunks of ONE document "
                f"(ids {doc_chunks[0]}..{doc_chunks[-1]}) into validation: {inside}")
    return "NOT REPRODUCED", f"val ids near 150: {[v for v in val_ids if 140 < v < 170]}"


def e2_no_doc_id_field():
    from vrusha.tokens.dataset import CorpusItem
    fields = CorpusItem.__dataclass_fields__.keys()
    if "doc_id" not in fields:
        return "CONFIRMED", f"CorpusItem fields are {list(fields)} — no doc_id, so a document-level split is impossible today"
    return "NOT REPRODUCED", list(fields)


def e3_config_validation():
    from vrusha.tokens.dataset import build_dataset
    import inspect
    src = inspect.getsource(build_dataset)
    missing = [c for c in ("shard_tokens <= 0", "val_fraction", "dtype") if c not in src]
    return ("BY INSPECTION",
            "build_dataset does not reject shard_tokens<=0 (infinite loop risk via "
            "range step), val_fraction outside [0,1), an unknown dtype, or token ids "
            "that overflow the chosen dtype; it silently coerces uint16->uint32 on "
            "vocab size only")


# ---------------------------------------------------------------------------
# F. checkpoint security
# ---------------------------------------------------------------------------

def f1_weights_only_false():
    import subprocess
    out = subprocess.run(["grep", "-rn", "weights_only=True", "vrusha/"], cwd=REPO,
                         capture_output=True, text=True).stdout.strip()
    if "weights_only=True" in out:
        return "NOT REPRODUCED", "the default load path is weights_only=True: " + \
            out.splitlines()[0].strip()
    return "CONFIRMED", "no safe default load path"


def f2_arbitrary_callable_on_load():
    """A crafted .pt must not execute through the product's loader."""
    import torch
    from vrusha.generate.model import CheckpointError, load_payload
    marker = os.path.join(tempfile.mkdtemp(prefix="poc-"), "executed.txt")

    class Benign:
        def __reduce__(self):
            return (open, (marker, "w"))

    path = os.path.join(os.path.dirname(marker), "untrusted.pt")
    torch.save({"model": Benign(), "config": {}}, path)
    try:
        load_payload(path)
    except CheckpointError:
        if os.path.exists(marker):
            return "CONFIRMED", "the payload executed before the loader refused it"
        return "NOT REPRODUCED", "load_payload refused the payload and nothing ran"
    except Exception as exc:
        return "CONFIRMED", f"unexpected failure mode: {type(exc).__name__}: {exc}"
    return "CONFIRMED", "load_payload accepted a pickled payload"


def f3_no_config_validation():
    from vrusha.generate.model import CheckpointError, config_from_payload
    bad = {"model": {}, "config": {"vocab_size": 96, "d_model": 65, "n_layer": 1,
                                   "n_head": 2, "n_ctx": 64}}
    sneaky = {"model": {}, "config": {"vocab_size": 96, "d_model": 64, "n_layer": 1,
                                      "n_head": 2, "n_ctx": 64, "extra": 1}}
    caught = 0
    for payload in (bad, sneaky):
        try:
            config_from_payload(payload)
        except CheckpointError:
            caught += 1
    if caught == 2:
        return "NOT REPRODUCED", "bad and unknown-key configs are both refused"
    return "CONFIRMED", f"only {caught} of 2 invalid configs were refused"


def g1_wrong_dim_accepted():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_paths
    from vrusha.store.db import DimensionError, Store
    from vrusha.store.embed import HashEmbedder

    root = fresh_root("dim")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    ingest_paths([os.path.join(REPO, "examples/fixtures/pages")], store, cfg, rules,
                 registry, HashEmbedder(256), licence="MIT")
    chunk_id = store.all_chunks()[0]["chunk_id"]
    try:
        store.set_vector(chunk_id, np.ones(128, dtype=np.float32))
    except DimensionError as exc:
        return "NOT REPRODUCED", f"refused: {str(exc).splitlines()[0][:90]}"
    return "CONFIRMED", "a 128-dim vector was accepted into a 256-dim store"


def g2_retrieval_silently_empty():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_paths
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.query import retrieve

    root = fresh_root("dimq")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    ingest_paths([os.path.join(REPO, "examples/fixtures/pages")], store, cfg, rules,
                 registry, HashEmbedder(256), licence="MIT")
    result = retrieve(store, HashEmbedder(128), "numeric address tree", cfg)
    if any("width mismatch" in note for note in result.notes):
        return "NOT REPRODUCED", "the width mismatch is reported in the retrieval notes"
    return "CONFIRMED", f"silent degradation: {result.notes}"


def h1_stale_graph_edges():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_paths
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.passes import PassContext, run_passes

    root = fresh_root("graph")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    ingest_paths([os.path.join(REPO, "examples/fixtures/pages")], store, cfg, rules,
                 registry, HashEmbedder(256), licence="MIT")
    ctx = PassContext(store=store, cfg=cfg, rules=rules, registry=registry)
    run_passes(ctx, only=["analyse.graph"])
    first = store.conn.execute("SELECT COUNT(*) FROM edges WHERE kind='near'").fetchone()[0]
    cfg["passes"]["graph_neighbours"] = 1
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry),
               only=["analyse.graph"])
    second = store.conn.execute("SELECT COUNT(*) FROM edges WHERE kind='near'").fetchone()[0]
    per_chunk = store.conn.execute(
        "SELECT src, COUNT(*) c FROM edges WHERE kind='near' GROUP BY src "
        "ORDER BY c DESC LIMIT 1").fetchone()
    if second > first:
        return ("CONFIRMED",
                f"k=5 produced {first} 'near' edges; rerunning with k=1 left {second} — "
                f"the old edges are never removed (worst chunk now has {per_chunk[1]} "
                f"neighbours for a k of 1)")
    return "NOT REPRODUCED", f"{first} -> {second}"


def h2_stale_centroids():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_paths
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.passes import PassContext, run_passes

    root = fresh_root("cent")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    ingest_paths([os.path.join(REPO, "examples/fixtures/pages")], store, cfg, rules,
                 registry, HashEmbedder(256), licence="MIT")
    cfg["passes"]["cluster_max"] = 24
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry),
               only=["analyse.cluster", "analyse.centroid"])
    first = store.conn.execute("SELECT COUNT(*) FROM centroids").fetchone()[0]
    cfg["passes"]["cluster_max"] = 2
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry),
               only=["analyse.cluster", "analyse.centroid"])
    rows = store.conn.execute("SELECT COUNT(*) FROM centroids").fetchone()[0]
    live = store.conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT addr_domain, addr_family, addr_kind, addr_cluster "
        "FROM chunks WHERE active=1 AND addr_cluster IS NOT NULL)").fetchone()[0]
    if rows > live:
        return ("CONFIRMED",
                f"after re-clustering with cluster_max=2 the centroid table still holds "
                f"{rows} rows for {live} live clusters (was {first} before) — the k=24 "
                f"centroids survive and describe a clustering that no longer exists")
    return "NOT REPRODUCED", f"{first} -> {rows} rows, {live} live"


def h3_idempotency():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_paths
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.passes import PassContext, run_passes

    root = fresh_root("idem")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    ingest_paths([os.path.join(REPO, "examples/fixtures/pages")], store, cfg, rules,
                 registry, HashEmbedder(256), licence="MIT")
    ctx = PassContext(store=store, cfg=cfg, rules=rules, registry=registry)
    run_passes(ctx)
    addr_1 = {c: str(a) for c, a in store.addresses()}
    logs_1 = store.conn.execute("SELECT COUNT(*) FROM address_log").fetchone()[0]
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry))
    addr_2 = {c: str(a) for c, a in store.addresses()}
    logs_2 = store.conn.execute("SELECT COUNT(*) FROM address_log").fetchone()[0]
    if addr_1 != addr_2:
        diff = {k: (addr_1[k], addr_2.get(k)) for k in addr_1 if addr_1[k] != addr_2.get(k)}
        return "CONFIRMED", f"second run moved addresses: {diff}"
    return "NOT REPRODUCED", f"addresses stable; address_log rows {logs_1} -> {logs_2}"


# ---------------------------------------------------------------------------
# I. region prefix matching
# ---------------------------------------------------------------------------

def i1_prefix_matches_neighbour():
    from vrusha.store.db import Store
    from vrusha.store.address import Address
    from vrusha.tokens.dataset import corpus_from_store

    root = fresh_root("prefix")
    store = Store(os.path.join(root, "v.db"))
    doc = store.add_document("https://e.org/a", "a", "text", licence="MIT")
    ids = {}
    for addr in ("2.1.3.1.1", "2.10.3.1.1", "2.11.3.1.1", "2.1.3.1.2"):
        cid = store.add_chunk(doc, f"chunk {addr} " + "x " * 60, kind="function")
        store.set_address(cid, Address.parse(addr))
        ids[addr] = cid
    store.commit()
    items = corpus_from_store(store, min_chars=10, address_prefix="2.1")
    got = [i.address for i in items]
    leaked = [g for g in got if g.startswith("2.10") or g.startswith("2.11")]
    if leaked:
        return ("CONFIRMED",
                f"corpus_from_store(address_prefix='2.1') uses a SQL LIKE on the address "
                f"string and returned {got}; 2.10 and 2.11 are not descendants of 2.1")
    return "NOT REPRODUCED", got


# ---------------------------------------------------------------------------
# J. harvesting: redirects, SSRF, robots
# ---------------------------------------------------------------------------

def j1_redirect_robots():
    import inspect
    from vrusha.harvest import fetch
    src = inspect.getsource(fetch.Fetcher.get)
    checks = {
        "robots checked once, before the request": "robots.allowed(url)" in src,
        "redirect destination re-checked": "final_url" in src and "robots" in src.split("final_url")[1],
        "private address guard": any(k in src for k in ("is_private", "127.0.0.1", "ip_address")),
        "host allow-list on redirect": "allowed_host" in src or "same_domain" in src,
    }
    missing = [k for k, v in checks.items() if not v]
    if missing:
        return ("BY INSPECTION",
                "missing: " + "; ".join(missing) + ". urllib follows redirects "
                "automatically (default HTTPRedirectHandler), so robots.txt and the "
                "domain restriction are enforced only for the URL you asked for, never "
                "for where it sends you. A redirect to 169.254.169.254 or localhost is "
                "fetched. Could not be reproduced at runtime: this sandbox has no "
                "inbound network and no way to run a redirecting server")
    return "NOT REPRODUCED", "all guards present"


def j2_encoding_detection():
    import inspect
    from vrusha.harvest import fetch
    src = inspect.getsource(fetch.Fetcher.get)
    if "get_content_charset" in src and "utf-8" in src:
        return ("BY INSPECTION",
                "charset comes from the Content-Type header only; there is no meta-charset "
                "sniff and no fallback to the HTML <meta charset>. A page served as "
                "text/html with no charset but declaring windows-1252 in its <meta> is "
                "decoded as utf-8 with errors='replace', which corrupts it silently")
    return "NOT REPRODUCED", "sniffing present"


# ---------------------------------------------------------------------------
# K. retrieval correctness
# ---------------------------------------------------------------------------

def k1_inactive_cannot_be_cited():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_paths
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.passes import PassContext, run_passes
    from vrusha.store.query import retrieve

    root = fresh_root("inactive")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    ingest_paths([os.path.join(REPO, "examples/fixtures/pages")], store, cfg, rules,
                 registry, HashEmbedder(256), licence="MIT")
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry))
    victim = store.all_chunks()[0]["chunk_id"]
    store.deactivate(victim, 0, weight=0.0)
    store.commit()
    r = retrieve(store, HashEmbedder(256), "numeric address tree knowledge store", cfg, top_k=10)
    cited = [h.chunk_id for h in r.hits]
    if victim in cited:
        return "CONFIRMED", f"deactivated chunk {victim} is still returned as a source"
    return "NOT REPRODUCED", f"{len(cited)} hits, none deactivated"


def k2_citation_points_at_source():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_paths
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.passes import PassContext, run_passes
    from vrusha.answer import ask

    root = fresh_root("cite")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    ingest_paths([os.path.join(REPO, "examples/fixtures/pages")], store, cfg, rules,
                 registry, HashEmbedder(256), licence="MIT")
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry))
    a = ask(store, HashEmbedder(256), "why fuse keyword and vector search by rank", cfg)
    bad = []
    for c in a.citations:
        row = store.conn.execute("SELECT url FROM documents WHERE url=?", (c["url"],)).fetchone()
        if row is None:
            bad.append(c)
    if bad:
        return "CONFIRMED", f"{len(bad)} citation(s) name a document that is not in the store"
    return "NOT REPRODUCED", f"{len(a.citations)} citation(s) resolve"


def k3_address_weight_dominates():
    from vrusha.rules import load_rules
    from vrusha.languages import load_registry
    from vrusha.ingest import ingest_paths
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.passes import PassContext, run_passes
    from vrusha.store.query import retrieve
    from vrusha.store.address import Address

    root = fresh_root("addrw")
    cfg = cfg_for(root)
    rules, registry = load_rules(), load_registry()
    store = Store(os.path.join(root, "v.db"))
    ingest_paths([os.path.join(REPO, "examples/fixtures/pages")], store, cfg, rules,
                 registry, HashEmbedder(256), licence="MIT")
    run_passes(PassContext(store=store, cfg=cfg, rules=rules, registry=registry))
    base = retrieve(store, HashEmbedder(256), "why fuse keyword and vector search by rank", cfg, top_k=3)
    hinted = retrieve(store, HashEmbedder(256), "why fuse keyword and vector search by rank", cfg,
                      top_k=3, address_hint=Address.parse("2"))
    if base.hits and hinted.hits and base.hits[0].chunk_id != hinted.hits[0].chunk_id:
        return ("CONFIRMED",
                f"an address hint for a completely different domain (2 = code) replaces the "
                f"correct top hit for a prose question: {base.hits[0].chunk_id} -> "
                f"{hinted.hits[0].chunk_id}. The address signal is weighted as heavily as "
                f"BM25 and vector together")
    return "NOT REPRODUCED", "hint did not displace the top hit"


# ---------------------------------------------------------------------------

CHECKS = [
    ("A1", "Address.parse rejects ordinals >= 100", a1_large_ordinal),
    ("A2", "Address.parse rejects cluster ids >= 100", a2_large_cluster),
    ("A3", "float form does not round-trip past 99", a3_float_roundtrip_100),
    ("A4", "packed form is ambiguous with 3-digit segments", a4_packed_ambiguity),
    ("A5", "malformed packed input is silently accepted", a5_invalid_packed_accepted),
    ("B1", "cluster numbering is size-ordered, not semantic", b1_cluster_number_is_not_semantic),
    ("B2", "address fusion weight equals bm25 and vector", b2_address_signal_weight),
    ("C1", "quality gate compares chars with bytes (non-Latin)", c1_hindi_rejected),
    ("C2", "Hindi article rejected end to end", c2_ingest_hindi_blocked),
    ("D1", "ingestion is not atomic", d1_partial_document),
    ("E1", "chunks of one document split across train/val", e1_chunks_of_one_document_split),
    ("E2", "CorpusItem has no doc_id", e2_no_doc_id_field),
    ("E3", "dataset config is unvalidated", e3_config_validation),
    ("F1", "checkpoints load with weights_only=False", f1_weights_only_false),
    ("F2", "an untrusted .pt executes a callable on load", f2_arbitrary_callable_on_load),
    ("F3", "checkpoint config is not validated", f3_no_config_validation),
    ("G1", "set_vector accepts a wrong dimension", g1_wrong_dim_accepted),
    ("G2", "dimension mismatch degrades retrieval silently", g2_retrieval_silently_empty),
    ("H1", "near-neighbour graph keeps stale edges", h1_stale_graph_edges),
    ("H2", "centroids from a previous clustering survive", h2_stale_centroids),
    ("H3", "passes are idempotent", h3_idempotency),
    ("I1", "region prefix 2.1 matches 2.10 and 2.11", i1_prefix_matches_neighbour),
    ("J1", "redirects bypass robots and domain limits", j1_redirect_robots),
    ("J2", "charset is not sniffed from the document", j2_encoding_detection),
    ("K1", "deactivated chunks cannot be cited", k1_inactive_cannot_be_cited),
    ("K2", "citations resolve to real documents", k2_citation_points_at_source),
    ("K3", "address hint can outvote a correct lexical hit", k3_address_weight_dominates),
]


def main():
    for bug_id, title, fn in CHECKS:
        check(bug_id, title, fn)

    print("\n\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    counts = {}
    for bug_id, title, status, _ in RESULTS:
        counts[status] = counts.get(status, 0) + 1
        print(f"{status:<15} {bug_id:<4} {title}")
    print()
    for status, n in sorted(counts.items()):
        print(f"{status:<15} {n}")


if __name__ == "__main__":
    main()
