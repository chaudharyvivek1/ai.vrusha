"""Every work, and its benchmark. One list, so nothing ships unmeasured.

The user's rule: each piece of work is tested *and* benchmarked. A test says the
behaviour is right; a benchmark says what it costs. A module with only the first
is a module whose cost nobody knows until it is in production.

`tests/test_benchmarks.py` checks that every module in `vrusha/` is either
measured here or listed in `EXEMPT` **with a reason**. That is what makes the
rule enforceable rather than aspirational: a new module with no benchmark fails
CI.

Each entry returns a dict of numbers. `python -m bench.registry` runs them all.
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from typing import Callable, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: Modules with no benchmark, and why. An entry here is a decision, not an
#: oversight -- and the reason is read by the coverage test, so "because" has to
#: be a sentence.
EXEMPT: Dict[str, str] = {
    "vrusha/__init__.py": "package marker",
    "vrusha/__main__.py": "entry point",
    "vrusha/cli.py": "command wiring; the commands it wires are measured through their modules",
    "vrusha/config.py": "constants",
    "vrusha/doctor.py": "reports the environment; it measures the machine, not itself",
    "vrusha/report.py": "renders numbers produced elsewhere",
    "vrusha/languages/__init__.py": "registry loading",
    "vrusha/rules/__init__.py": "registry loading",
    "vrusha/rules/loader.py": "registry loading",
    "vrusha/store/__init__.py": "package marker",
    "vrusha/tools/__init__.py": "adapter plumbing; each tool is measured by its own CLI",
    "vrusha/tools/cutforge.py": "a specification, not an engine -- nothing to run",
    "vrusha/tools/dubforge.py": "an adapter; the tool is external and measured by its own repo",
    "vrusha/tools/forge3d.py": "an adapter; the tool is external and measured by its own repo",
    # measured through the module that owns the behaviour
    "vrusha/answer.py": "composes an answer from hits; `retrieval` measures the pipeline end to end",
    "vrusha/store/address.py": "the address model; `address` benchmarks the ranking that uses it",
    "vrusha/store/ann.py": "measured by bench/ann_bench.py and bench/real_corpus.py, which need minutes not milliseconds",
    "vrusha/store/backends.py": "measured by bench/headtohead.py and bench/live_merge_test.py",
    "vrusha/store/router.py": "the decision is a policy, not a cost; bench/headtohead.py measures its choices",
    "vrusha/store/vec0.py": "measured against the exact scan in tests/test_vec0.py; the comparison is correctness, not speed",
    "vrusha/store/db.py": "the substrate; every other benchmark measures it in use",
    "vrusha/store/embed.py": "measured by bench/embedders.py, which compares three embedders on quality",
    "vrusha/store/chunk.py": "pure text splitting; `ingest` measures it in place",
    "vrusha/store/records.py": "measured by `extract`",
    "vrusha/store/extract.py": "measured by `extract`",
    "vrusha/store/pdf.py": "measured by `extract` on the PDF fixture",
    "vrusha/store/jsonld.py": "measured by `extract` on the JSON-LD fixture",
    "vrusha/store/formula.py": "a compile-and-evaluate step over a column; correctness is the claim, and it is tested",
    "vrusha/store/relations.py": "measured by `extract` and by tests/test_relations.py on fixtures",
    "vrusha/store/entities.py": "fuzzy matching over small sets; tests/test_entities.py covers the behaviour",
    "vrusha/store/mathquery.py": "canonicalisation is deterministic; tests/test_math.py covers the equivalences",
    "vrusha/store/passes.py": "batch work over the whole store; bench/suite.py measures the pipeline phases",
    "vrusha/store/sandbox.py": "a process boundary; the cost is the child process, which the test measures against the timeout",
    "vrusha/store/export.py": "a one-off export, not a hot path",
    "vrusha/harvest/fetch.py": "network-bound; a local benchmark would measure the loopback, not the web",
    "vrusha/harvest/net.py": "network-bound, as above",
    "vrusha/harvest/search.py": "network-bound, as above",
    "vrusha/harvest/gates.py": "runs inside `ingest`, which is measured",
    "vrusha/harvest/charset.py": "pure decoding; tests/test_wave5_network.py covers the hard cases",
    "vrusha/harvest/__init__.py": "package marker",
    "vrusha/verify.py": "compares stored text to a refetch; network-bound",
    "vrusha/research.py": "the whole loop, network-bound; `retrieval` measures its local half",
    "vrusha/serve.py": "measured by `serve`, which makes a real request",
    "vrusha/generate/__init__.py": "package marker",
    "vrusha/generate/model.py": "needs torch, which is not installed in CI; measured when it is",
    "vrusha/generate/train.py": "needs torch, as above",
    "vrusha/generate/sample.py": "needs torch, as above",
    "vrusha/generate/compose.py": "template assembly; `retrieval` covers it through the answer path",
    "vrusha/tokens/__init__.py": "package marker",
    "vrusha/tokens/bpe.py": "training a tokeniser is a batch job; docs/MODEL.md records its numbers",
    "vrusha/tokens/dataset.py": "a batch job, as above",
    "vrusha/languages/registry.py": "registry loading; docs/PASSES.md records the profile count",
}


def _timeit(function: Callable[[], object], repeats: int = 5) -> Dict[str, float]:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000.0)
    return {"p50_ms": statistics.median(samples), "max_ms": max(samples)}


# ---------------------------------------------------------------------------
# the benchmarks
# ---------------------------------------------------------------------------


def bench_blackboard() -> Dict[str, float]:
    """How fast can agents append, and what does a delta read cost?"""
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.db import Store

    path = os.path.join(tempfile.mkdtemp(), "board.db")
    store = Store(path)
    board = Blackboard(store)
    for index in range(2000):
        board.append("run", task=f"t{index % 20}", agent="L4/a",
                     summary=f"finding {index}", refs=["table:1"])

    append = _timeit(lambda: board.append("run", summary="bench"), repeats=20)
    delta = _timeit(lambda: board.since("run", 1990, limit=50), repeats=20)
    replay = _timeit(lambda: board.replay("run"), repeats=3)
    rows = store.conn.execute("SELECT COUNT(*) FROM x_board").fetchone()[0]
    board.close()
    store.close()
    return {"append_ms": append["p50_ms"], "delta_read_ms": delta["p50_ms"],
            "replay_ms": replay["p50_ms"], "entries": rows}


def bench_claims() -> Dict[str, float]:
    """What does arbitration cost when 64 agents want the same task?"""
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store

    store = Store(os.path.join(tempfile.mkdtemp(), "claims.db"))
    board = Blackboard(store)
    claims = Claims(board)
    claim = _timeit(lambda: claims.claim("run", "task-1", "L4/a", ttl=60),
                    repeats=20)
    summary = _timeit(lambda: claims.summary("run"), repeats=20)
    board.close()
    store.close()
    return {"claim_ms": claim["p50_ms"], "summary_ms": summary["p50_ms"]}


def bench_registry() -> Dict[str, float]:
    """Registering, beating and sweeping, at the 64-agent cap."""
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.db import Store
    from vrusha.store.registry import Registry

    store = Store(os.path.join(tempfile.mkdtemp(), "registry.db"))
    board = Blackboard(store)
    registry = Registry(board)
    registry.open_run("run")
    for index in range(64):
        registry.register("run", f"agent-{index}", skill=f"s{index % 8}")

    register = _timeit(lambda: registry.register("run", "agent-0"), repeats=20)
    beat = _timeit(lambda: registry.heartbeat("run", "agent-0"), repeats=20)
    # read the summary *before* the sweep: a sweep with stale_after=0 marks
    # everything dead, so measuring `live` afterwards reports zero and hides the
    # thing the benchmark is about
    summary = registry.summary("run")
    sweep = _timeit(lambda: registry.sweep("run", stale_after=0, now=time.time()),
                    repeats=5)
    board.close()
    store.close()
    return {"register_ms": register["p50_ms"], "heartbeat_ms": beat["p50_ms"],
            "sweep_ms": sweep["p50_ms"], "live": float(summary["live"])}


def bench_rerank() -> Dict[str, float]:
    """The second stage over twenty candidates."""
    from vrusha.rerank import LexicalReranker, NoOpReranker
    from vrusha.store.query import Hit

    hits = [Hit(chunk_id=i, address=None, text=("address routing index " * 30),
                symbol="", kind="prose", lang="en", url=f"https://a.test/{i}",
                title="ANN", licence="MIT",
                scores={"primary": 1.0, "fused": 1.0 - i * 0.01})
            for i in range(20)]
    query = "how does the index route a query"
    lexical = LexicalReranker()
    return {"noop_ms": _timeit(lambda: NoOpReranker().rerank(query, hits, 10),
                               repeats=20)["p50_ms"],
            "lexical_ms": _timeit(lambda: lexical.rerank(query, hits, 10),
                                  repeats=20)["p50_ms"],
            "candidates": float(len(hits))}


def bench_address() -> Dict[str, float]:
    """The address ranking, warm and cold, from `bench/address.py`'s finding."""
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.store import query as query_module
    from vrusha.store.address import Address
    from vrusha.store.db import Store

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    doc = store.add_document(url="https://a.test/1", title="a", text="b " * 100)
    for index in range(2000):
        chunk = store.add_chunk(doc, f"chunk {index} " + "text " * 8, kind="prose")
        store.set_address(chunk, Address((1, 1 + index % 7, 1 + index % 3,
                                          1 + index % 40, index % 900)))
    store.commit()
    hint = Address.parse("1.2.3.1.100")
    query_module._ADDRESS_CACHE.clear()
    query_module._address_ranking(store, hint, 60)          # warm the matrix
    warm = _timeit(lambda: query_module._address_ranking(store, hint, 60),
                   repeats=20)
    store.close()
    return {"warm_ms": warm["p50_ms"], "rows": 2000.0}


def bench_retrieval() -> Dict[str, float]:
    """A whole query, end to end, on this repository's documentation."""
    from vrusha import evaluate
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.harvest.fetch import Document
    from vrusha.ingest import ingest_documents
    from vrusha.languages import load_registry
    from vrusha.rules import load_rules
    from vrusha.store.db import Store
    from vrusha.store.embed import get_embedder

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    documents = []
    for name in sorted(os.listdir(os.path.join(ROOT, "docs"))):
        if not name.endswith(".md"):
            continue
        path = os.path.join(ROOT, "docs", name)
        with open(path, encoding="utf-8") as handle:
            documents.append(Document(url=f"file://{path}", title=name,
                                      status=200, text=handle.read()))
    embedder = get_embedder(cfg)
    ingest_documents(documents, store, cfg, load_rules(), load_registry(),
                     embedder, licence="MIT", verbose=False)
    items = evaluate.load_set("docs")
    outcomes = evaluate.run(store, embedder, cfg, items, rerank_off=True)
    summary = evaluate.summarise(outcomes)
    store.close()
    # The embedder is part of the measurement. Without it the same benchmark
    # reads 0.70 recall@5 with model2vec and 0.47 with the hash fallback, and
    # nothing in the output says which -- a number without a method.
    return {"query_ms": summary["seconds"] * 1000.0,
            "recall@5": summary["recall@5"], "ndcg@10": summary["ndcg@10"],
            "questions": float(summary["questions"]),
            "model2vec": 1.0 if getattr(embedder, "name", "") == "model2vec" else 0.0}


def bench_ingest() -> Dict[str, float]:
    """How long does a document take to become chunks and vectors?"""
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.harvest.fetch import Document
    from vrusha.ingest import ingest_documents
    from vrusha.languages import load_registry
    from vrusha.rules import load_rules
    from vrusha.store.db import Store
    from vrusha.store.embed import get_embedder

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    documents = []
    for name in sorted(os.listdir(os.path.join(ROOT, "docs"))):
        if not name.endswith(".md"):
            continue
        path = os.path.join(ROOT, "docs", name)
        with open(path, encoding="utf-8") as handle:
            documents.append(Document(url=f"file://{path}", title=name,
                                      status=200, text=handle.read()))
    embedder = get_embedder(cfg)
    started = time.perf_counter()
    report = ingest_documents(documents, store, cfg, load_rules(),
                              load_registry(), embedder, licence="MIT",
                              verbose=False)
    seconds = time.perf_counter() - started
    store.close()
    return {"docs_per_second": len(documents) / max(1e-9, seconds),
            "chunks": float(report.chunks),
            "ms_per_doc": 1000.0 * seconds / max(1, len(documents))}


def bench_extract() -> Dict[str, float]:
    """Fixtures to typed rows: the extraction layer's own cost."""
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.store import records
    from vrusha.store.db import Store

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    fixtures = os.path.join(ROOT, "examples", "fixtures", "tables")
    pages = []
    for name in sorted(os.listdir(fixtures)):
        path = os.path.join(fixtures, name)
        if os.path.isfile(path):
            pages.append((name, path))
    started = time.perf_counter()
    tables = 0
    for index, (name, path) in enumerate(pages):
        doc = store.add_document(url=f"https://a.test/{name}", title=name,
                                 text="fixture " * 50)
        try:
            with open(path, encoding="utf-8", errors="ignore") as handle:
                body = handle.read()
        except OSError:
            continue
        extracted = records.extract_document(store, doc, body, f"https://a.test/{name}")
        tables += int(extracted.get("tables", 0))
    store.commit()
    seconds = time.perf_counter() - started
    store.close()
    return {"files_per_second": len(pages) / max(1e-9, seconds),
            "tables": float(tables), "ms_per_file": 1000.0 * seconds / max(1, len(pages))}


def bench_serve() -> Dict[str, float]:
    """A request through the JSON API, over a real socket."""
    import json
    import urllib.request

    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.harvest.fetch import Document
    from vrusha.ingest import ingest_documents
    from vrusha.languages import load_registry
    from vrusha.rules import load_rules
    from vrusha.serve import serve_in_thread
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    ingest_documents([Document(url="https://a.test/1", title="ann", status=200,
                               text="the vector index uses ivf routing. " * 40)],
                     store, cfg, load_rules(), load_registry(),
                     HashEmbedder(64), licence="MIT", verbose=False)
    store.close()
    httpd, _thread = serve_in_thread(cfg, HashEmbedder(64), port=0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    body = json.dumps({"text": "how does the index route", "k": 3}).encode()

    def one_request():
        request = urllib.request.Request(base + "/search", method="POST",
                                         data=body,
                                         headers={"content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()

    try:
        timings = _timeit(one_request, repeats=20)
    finally:
        httpd.shutdown()
        httpd.server_close()
    return {"request_ms": timings["p50_ms"], "max_ms": timings["max_ms"]}


def bench_index_delta() -> Dict[str, float]:
    """What an append costs, against what a rebuild costs.

    The whole point of delta segments: adding vectors must be O(new) and not
    O(all). This measures both on the same store, and reports the ratio.
    """
    import numpy as np

    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.store import ann as ann_mod
    from vrusha.store import delta as delta_mod
    from vrusha.store.db import Store

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    doc = store.add_document(url="https://a.test/1", title="t", text="x " * 60)
    rng = np.random.default_rng(3)
    dim = 64
    for index in range(4000):
        chunk = store.add_chunk(doc, f"chunk {index} " + "text " * 6, kind="prose")
        vector = rng.normal(size=dim).astype(np.float32)
        store.set_vector(chunk, vector / np.linalg.norm(vector))
    store.commit()

    index_dir = resolve(cfg, "index")
    os.makedirs(index_dir, exist_ok=True)
    started = time.perf_counter()
    built = ann_mod.AnnIndex.build(store)
    built.save(index_dir)
    rebuild_ms = (time.perf_counter() - started) * 1000.0

    new_ids = []
    for index in range(200):
        chunk = store.add_chunk(doc, f"new {index} " + "text " * 6, kind="prose")
        vector = rng.normal(size=dim).astype(np.float32)
        store.set_vector(chunk, vector / np.linalg.norm(vector))
        new_ids.append(chunk)
    store.commit()

    started = time.perf_counter()
    delta_mod.append(store, index_dir, new_ids)
    append_ms = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    delta_mod.upsert(store, index_dir, new_ids[:50])
    upsert_ms = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    delta_mod.delete(store, index_dir, new_ids[50:60])
    delete_ms = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    compacted = delta_mod.safe_compact(store, index_dir, cfg)
    compact_ms = (time.perf_counter() - started) * 1000.0

    query = rng.normal(size=dim).astype(np.float32)
    query /= np.linalg.norm(query)
    search_ms = _timeit(lambda: delta_mod.search(index_dir, query, 10,
                                                 base=built, store=store),
                        repeats=10)["p50_ms"]
    store.close()
    return {"rebuild_ms": rebuild_ms, "append_ms": append_ms,
            "upsert_ms": upsert_ms, "delete_ms": delete_ms,
            "compact_ms": compact_ms,
            "ratio": rebuild_ms / max(1e-9, append_ms),
            "search_ms": search_ms, "new_vectors": 200.0,
            "compacted": 1.0 if compacted.get("compacted") else 0.0}


def bench_snapshot() -> Dict[str, float]:
    """Pinning, releasing and collecting generations — the reader's side of a
    compaction."""
    from vrusha.store import snapshot

    index_dir = tempfile.mkdtemp()
    for _ in range(5):
        snapshot.publish(index_dir)
    pin = _timeit(lambda: snapshot.pin(index_dir).release(), repeats=20)
    read = _timeit(lambda: snapshot.read_current(index_dir), repeats=50)
    collect = _timeit(lambda: snapshot.gc(index_dir, keep=2), repeats=10)
    return {"pin_ms": pin["p50_ms"], "read_current_ms": read["p50_ms"],
            "gc_ms": collect["p50_ms"]}


def bench_entities() -> Dict[str, float]:
    """Entities from prose: how fast, and how much of the corpus it reads."""
    from vrusha.store import prose

    corpus = []
    for name in sorted(os.listdir(os.path.join(ROOT, "docs"))):
        if name.endswith(".md"):
            with open(os.path.join(ROOT, "docs", name), encoding="utf-8") as handle:
                corpus.append(handle.read())
    text = "\n".join(corpus)
    started = time.perf_counter()
    found = prose.extract(text)
    seconds = time.perf_counter() - started
    timings = _timeit(lambda: prose.extract(corpus[0]), repeats=10)
    return {"entities": float(len(found)),
            "chars_per_second": len(text) / max(1e-9, seconds),
            "doc_ms": timings["p50_ms"] * 1000.0,
            "kinds": float(len(prose.by_kind(found)))}


def bench_edges() -> Dict[str, float]:
    """Relations from prose, with evidence — how many, and how fast."""
    from vrusha.store import edges as edges_mod

    corpus = []
    for name in sorted(os.listdir(os.path.join(ROOT, "docs"))):
        if name.endswith(".md"):
            with open(os.path.join(ROOT, "docs", name), encoding="utf-8") as handle:
                corpus.append(handle.read())
    text = "\n".join(corpus)
    started = time.perf_counter()
    found = edges_mod.extract(text)
    seconds = time.perf_counter() - started
    with_evidence = sum(1 for edge in found if edge.sentence)

    # The documentation corpus is technical prose, and these patterns are business
    # relations -- so it produces no edges. That is a fact worth reporting, not a
    # zero to hide: the fixture below is the corpus the extractor is *for*.
    fixture = (
        "Acme Corporation acquired Beta Limited in March. "
        "Beta Limited supplies steel to Gamma Industries. "
        "Gamma Industries is a subsidiary of Acme Corporation. "
        "Dr. Meera Rao joined the Reserve Bank of India. "
        "Acme Corporation partnered with Delta Systems."
    )
    fixture_edges = edges_mod.extract(fixture)
    return {"edges": float(len(found)),
            "with_evidence": float(with_evidence),
            "chars_per_second": len(text) / max(1e-9, seconds),
            "predicates": float(len({edge.predicate for edge in found})),
            "fixture_edges": float(len(fixture_edges)),
            "fixture_evidence": float(sum(1 for e in fixture_edges if e.sentence))}


def bench_communities() -> Dict[str, float]:
    """Label propagation over the edge graph."""
    from vrusha.store import communities

    # a synthetic graph with a known shape: 20 clusters of 10, sparsely linked
    pairs = []
    for cluster in range(20):
        base = f"entity-{cluster}"
        for member in range(1, 10):
            pairs.append((base, f"{base}-{member}"))
    graph = communities.adjacency(pairs)
    timings = _timeit(lambda: communities.propagate(graph), repeats=5)
    labels = communities.propagate(graph)
    summary = communities.summarise(labels, graph)
    return {"nodes": float(summary["nodes"]),
            "communities": float(summary["communities"]),
            "ms": timings["p50_ms"], "edges": float(len(pairs))}


def bench_paths() -> Dict[str, float]:
    """Two-hop path search over a graph of a known shape."""
    import tempfile as _tempfile

    from vrusha.store import edges as edges_mod
    from vrusha.store import paths as paths_mod
    from vrusha.store.db import Store

    store = Store(_tempfile.mktemp(suffix=".db"))
    # 20 clusters of 10, chained: cluster i connects to cluster i+1 through one
    # edge, so a 2-hop path exists only where the chain links them
    rows = []
    for cluster in range(20):
        base = f"entity-{cluster}"
        for member in range(1, 10):
            rows.append((base, f"{base}-{member}"))
        if cluster:
            rows.append((f"entity-{cluster - 1}", base))
    edges_mod.store_edges(store, [
        edges_mod.Edge(subject=left, predicate="linked_to", object=right,
                       sentence=f"{left} linked to {right}.", confidence=0.8)
        for left, right in rows], doc_id=1)
    timings = _timeit(lambda: paths_mod.find(store, "entity-0", "entity-2",
                                             max_hops=2), repeats=20)
    found = paths_mod.find(store, "entity-0", "entity-2", max_hops=2)
    store.close()
    return {"ms": timings["p50_ms"], "edges": float(len(rows)),
            "hops": float(found["hops"]) if found else 0.0}


def bench_graphboost() -> Dict[str, float]:
    """The graph boost's cost, and whether it has anything to boost."""
    import tempfile as _tempfile

    from vrusha.store import communities, edges
    from vrusha.store import graphboost
    from vrusha.store.db import Store
    from vrusha.store.query import Hit

    store = Store(_tempfile.mktemp(suffix=".db"))
    # names prose extraction can actually see (capitalised, with a suffix), so the
    # query produces a community and the boost has something to do
    rows = []
    for index in range(200):
        rows.append((f"Acme Corporation {index}", f"Acme Corporation {index + 1}"))
    edges.store_edges(store, [
        edges.Edge(subject=left, predicate="linked_to", object=right,
                   sentence=f"{left} linked {right}.", confidence=0.8)
        for left, right in rows], doc_id=1)
    store.conn.execute("UPDATE x_edges SET chunk_id = (edge_id % 20) + 1")
    store.commit()
    communities.build(store)

    hits = [Hit(chunk_id=index, address=None, text="text", symbol="", kind="prose",
                lang="en", url=f"https://a.test/{index}", title="t", licence="MIT",
                scores={"primary": 1.0 - index * 0.01, "fused": 1.0 - index * 0.01})
            for index in range(1, 21)]
    query = "Acme Corporation 5"
    timings = _timeit(lambda: graphboost.boost(store, query, hits), repeats=20)
    boosted = graphboost.boost(store, query, hits)
    changed = sum(1 for before, after in zip(hits, boosted)
                  if before.chunk_id != after.chunk_id)
    store.close()
    return {"ms": timings["p50_ms"], "hits": float(len(hits)),
            "reordered": float(changed), "edges": float(len(rows))}


def _tempfile_name() -> str:
    import tempfile as _tempfile
    return _tempfile.mktemp(suffix=".db")


def bench_compaction() -> Dict[str, float]:
    """What compaction saves on a realistic run, and what it costs."""
    from vrusha.store import compaction
    from vrusha.store.blackboard import Entry

    made = []
    kinds = ["progress"] * 40 + ["finding"] * 10 + ["decision", "error", "artifact"]
    for index, kind in enumerate(kinds, start=1):
        entry = Entry(run="r", seq=index, task=f"t{index % 5}", kind=kind,
                      summary=f"{kind} {index} " + ("detail " * 80))
        if kind == "artifact":
            entry.refs = [f"file:{index}.csv"]
        made.append(entry)
    timings = _timeit(lambda: compaction.compact(made), repeats=10)
    summary = compaction.compact(made)
    verdict = compaction.verify(summary, made)
    # and the offload path: one oversized entry replaced by a reference
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.db import Store as _Store
    _store = _Store(_tempfile_name())
    _board = Blackboard(_store)
    big = Entry(run="r", seq=99, task="t1", kind="finding",
                summary="page " + ("word " * 25000))
    offload_ms = _timeit(lambda: compaction.offload(_board, [big], "r"),
                         repeats=5)["p50_ms"]
    _kept, offload_report = compaction.offload(_board, [big], "r")
    _board.close()
    _store.close()
    return {"entries": float(len(made)), "ms": timings["p50_ms"],
            "offload_ms": offload_ms,
            "offload_ratio": (offload_report["tokens_after"]
                              / max(1, offload_report["tokens_before"])),
            "tokens_before": float(summary.tokens_before),
            "tokens_after": float(summary.tokens_after),
            "ratio": float(summary.ratio),
            "verified": 1.0 if verdict["ok"] else 0.0}


def bench_anticipate() -> Dict[str, float]:
    """What a warm hit saves, and what a wrong guess costs."""
    from vrusha.store import anticipate
    from vrusha.store.query import Hit

    cache = anticipate.Cache()
    put = _timeit(lambda: cache.put("q", [Hit(chunk_id=1, address=None, text="t",
                                              symbol="", kind="prose", lang="en",
                                              url="u", title="t", licence="MIT",
                                              scores={})], now=0.0), repeats=50)
    hit = _timeit(lambda: cache.get("q", now=0.0), repeats=50)
    miss = _timeit(lambda: cache.get("absent", now=0.0), repeats=50)
    return {"put_ms": put["p50_ms"], "hit_ms": hit["p50_ms"],
            "miss_ms": miss["p50_ms"], "cached": float(len(cache)),
            "budget_ms": anticipate.BUDGET_MS, "max_tasks": float(anticipate.MAX_TASKS)}


def bench_orchestrator() -> Dict[str, float]:
    """What the loop itself costs, with the worker doing nothing."""
    from vrusha import orchestrator as orch
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.registry import Registry

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    claims = Claims(board)
    registry = Registry(board)
    tasks = [f"task-{index}" for index in range(10)]
    timings = _timeit(lambda: orch.run(store, board, claims, registry, cfg,
                                       "benchmark", worker=lambda name: "ok",
                                       tasks=tasks), repeats=3)
    result = orch.run(store, board, claims, registry, cfg, "benchmark",
                      worker=lambda name: "ok", tasks=tasks)
    store.close()
    return {"tasks": float(len(tasks)), "ms": timings["p50_ms"],
            "ms_per_task": timings["p50_ms"] / len(tasks),
            "done": float(result.done)}


def bench_pool() -> Dict[str, float]:
    """Does the pool help, or does it contend on one writer?

    The worker sleeps, so the measurement is about overlap rather than CPU. A
    single-writer blackboard means the bookkeeping cannot speed up; only the
    waiting can.
    """
    import time as _time

    from vrusha import orchestrator as orch
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.registry import Registry

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    claims = Claims(board)
    registry = Registry(board)

    def slow(name: str) -> str:
        _time.sleep(0.02)
        return f"did {name}"

    tasks = [f"t{index}" for index in range(8)]
    numbers = {}
    baseline = 0.0
    for workers in (1, 2, 4, 8):
        started = time.perf_counter()
        result = orch.run_pool(store, board, claims, registry, cfg, "pool",
                               worker=slow, tasks=tasks, workers=workers)
        elapsed = (time.perf_counter() - started) * 1000.0
        numbers[f"w{workers}_ms"] = elapsed
        numbers[f"w{workers}_done"] = float(result.done)
        if workers == 1:
            baseline = elapsed
    numbers["speedup"] = baseline / max(1e-9, numbers["w4_ms"])
    store.close()
    return numbers


def bench_remote() -> Dict[str, float]:
    """A remote agent's round trip: append and delta read over HTTP."""
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.remote import RemoteBoard
    from vrusha.serve import serve_in_thread
    from vrusha.store.embed import HashEmbedder

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    httpd, _thread = serve_in_thread(cfg, HashEmbedder(32), port=0)
    board = RemoteBoard(f"http://127.0.0.1:{httpd.server_address[1]}")
    try:
        for index in range(50):
            board.append("bench", summary=f"entry {index}")
        append = _timeit(lambda: board.append("bench", summary="x"), repeats=20)
        delta = _timeit(lambda: board.since("bench", 40), repeats=20)
    finally:
        httpd.shutdown()
        httpd.server_close()
    return {"append_ms": append["p50_ms"], "delta_ms": delta["p50_ms"],
            "entries": 50.0}


def bench_remote_loop() -> Dict[str, float]:
    """The loop in-process against the loop over HTTP, same tasks."""
    from vrusha import orchestrator as orch
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.serve import serve_in_thread
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder
    from vrusha.store.registry import Registry

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    httpd, _thread = serve_in_thread(cfg, HashEmbedder(32), port=0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    claims = Claims(board)
    registry = Registry(board)
    tasks = [f"t{index}" for index in range(8)]
    try:
        started = time.perf_counter()
        orch.run_pool(store, board, claims, registry, cfg, "bench",
                      worker=lambda name: "ok", tasks=tasks, workers=4)
        local_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        result = orch.run_remote(store, board, claims, registry, cfg, "bench",
                                 worker=lambda name: "ok", tasks=tasks,
                                 base_url=base, workers=4)
        remote_ms = (time.perf_counter() - started) * 1000.0
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()
    return {"local_ms": local_ms, "remote_ms": remote_ms,
            "overhead_x": remote_ms / max(1e-9, local_ms),
            "done": float(result.done), "tasks": float(len(tasks))}


def bench_compare() -> Dict[str, float]:
    """Four levels against one agent, on the same questions."""
    from vrusha import compare
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.harvest.fetch import Document
    from vrusha.ingest import ingest_documents
    from vrusha.languages import load_registry
    from vrusha.rules import load_rules
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.embed import get_embedder
    from vrusha.store.registry import Registry

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = tempfile.mkdtemp()
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    documents = []
    for name in sorted(os.listdir(os.path.join(ROOT, "docs"))):
        if not name.endswith(".md"):
            continue
        path = os.path.join(ROOT, "docs", name)
        with open(path, encoding="utf-8") as handle:
            documents.append(Document(url=f"file://{path}", title=name,
                                      status=200, text=handle.read()))
    embedder = get_embedder(cfg)
    ingest_documents(documents, store, cfg, load_rules(), load_registry(),
                     embedder, licence="MIT", verbose=False)
    board = Blackboard(store)
    result = compare.run(store, embedder, cfg, board, Claims(board),
                         Registry(board), run_id="bench-compare")
    store.close()
    return {"single_s": result.single_seconds,
            "orchestrated_s": result.orchestrated_seconds,
            "cost_ratio": result.cost_ratio,
            "quality_delta": result.quality_delta,
            "board_entries": float(result.board_entries),
            "questions": float(result.questions)}


def bench_render() -> Dict[str, float]:
    """Shell detection must be free, or it is a browser call in disguise."""
    from vrusha.harvest import render

    shell = ('<html><body><div id="root"></div>'
             + '<script src="/app.js"></script>' * 30 + "</body></html>")
    real = ("<html><body><p>" + "the licence gate refuses a page. " * 40
            + "</p></body></html>")
    detect = _timeit(lambda: render.looks_like_a_shell(shell), repeats=50)
    detect_real = _timeit(lambda: render.looks_like_a_shell(real), repeats=50)
    text = _timeit(lambda: render.visible_text(shell), repeats=50)
    return {"shell_us": detect["p50_ms"] * 1000.0,
            "real_us": detect_real["p50_ms"] * 1000.0,
            "text_us": text["p50_ms"] * 1000.0,
            "shell_bytes": float(len(shell))}


def bench_ocr() -> Dict[str, float]:
    """What an image costs to identify — the part that needs no system binary."""
    import io as _io

    from PIL import Image

    from vrusha.store import ocr

    buffer = _io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buffer, format="PNG")
    data = buffer.getvalue()
    detect = _timeit(lambda: ocr.looks_like_image(data), repeats=50)
    describe = _timeit(lambda: ocr.describe(data), repeats=20)
    return {"detect_us": detect["p50_ms"] * 1000.0,
            "describe_ms": describe["p50_ms"],
            "bytes": float(len(data))}


def bench_beir() -> Dict[str, float]:
    """Reading a BEIR set, and scoring one query against its labels."""
    from vrusha.eval_sets import beir

    loaded = beir.bundled()
    read = _timeit(lambda: beir.bundled(), repeats=10)
    ranking = [row["_id"] for row in loaded.corpus]
    relevant = loaded.qrels[loaded.queries[0]["_id"]]
    score = _timeit(lambda: beir.score(ranking, relevant, 10), repeats=50)
    return {"read_ms": read["p50_ms"], "score_us": score["p50_ms"] * 1000.0,
            "documents": float(loaded.documents),
            "queries": float(len(loaded.queries))}


BENCHMARKS: List[tuple] = [
    ("blackboard", bench_blackboard, "append, delta read, replay"),
    ("claims", bench_claims, "claim arbitration, summary"),
    ("registry", bench_registry, "register, heartbeat, sweep at 64 slots"),
    ("address", bench_address, "the address ranking, warm"),
    ("rerank", bench_rerank, "the second stage over 20 candidates"),
    ("retrieval", bench_retrieval, "a whole query, and its quality"),
    ("ingest", bench_ingest, "documents to chunks and vectors"),
    ("extract", bench_extract, "fixtures to typed tables"),
    ("serve", bench_serve, "one request through the JSON API"),
    ("index_delta", bench_index_delta, "append vs full rebuild"),
    ("snapshot", bench_snapshot, "pin, read and collect index generations"),
    ("entities", bench_entities, "entities from prose, over the whole corpus"),
    ("edges", bench_edges, "relations with evidence, over the whole corpus"),
    ("communities", bench_communities, "label propagation over a known graph"),
    ("paths", bench_paths, "two-hop search over a chained graph"),
    ("graphboost", bench_graphboost, "the bounded graph boost over 20 hits"),
    ("compaction", bench_compaction, "fold a run into a verified summary"),
    ("anticipate", bench_anticipate, "the prefetch cache: put, hit, miss"),
    ("orchestrator", bench_orchestrator, "the loop's own overhead per task"),
    ("pool", bench_pool, "1, 2, 4 and 8 workers on the same 8 tasks"),
    ("remote", bench_remote, "a remote agent's round trip over HTTP"),
    ("remote_loop", bench_remote_loop, "the loop in-process against over HTTP"),
    ("compare", bench_compare, "four levels against one agent, same questions"),
    ("render", bench_render, "shell detection, which must be free"),
    ("ocr", bench_ocr, "identifying an image, which needs no binary"),
    ("beir", bench_beir, "reading a BEIR set and scoring one query"),
]


def run_all() -> List[dict]:
    rows = []
    for name, function, what in BENCHMARKS:
        started = time.perf_counter()
        try:
            numbers = function()
            error = ""
        except Exception as exc:                 # noqa: BLE001 - report, do not hide
            numbers = {}
            error = f"{type(exc).__name__}: {exc}"
        rows.append({"name": name, "what": what, "numbers": numbers,
                     "error": error,
                     "seconds": round(time.perf_counter() - started, 2)})
    return rows


def main() -> int:
    rows = run_all()
    print(f"{'benchmark':<12}{'seconds':>9}  numbers")
    print("-" * 78)
    for row in rows:
        numbers = ", ".join(f"{key}={value:.2f}" if isinstance(value, float)
                            else f"{key}={value}"
                            for key, value in row["numbers"].items())
        print(f"{row['name']:<12}{row['seconds']:>9.2f}  {numbers or row['error']}")
    return 0 if all(not row["error"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
