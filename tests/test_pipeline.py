"""Pipeline tests: gates, chunking, the store, the passes, retrieval, verify.

One temporary store is built from the fixture pages, then every stage is
asserted against it. No network, no model.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.harvest.fetch import Document, harvest_local, html_to_text
from vrusha.harvest.gates import (
    band_keys,
    detect_licence,
    estimated_jaccard,
    evaluate,
    find_secrets,
    minhash_signature,
)
from vrusha.ingest import ingest_paths
from vrusha.languages import load_registry
from vrusha.rules import load_rules
from vrusha.store.chunk import chunk_text
from vrusha.store.db import Store
from vrusha.store.embed import HashEmbedder
from vrusha.store.passes import PassContext, run_passes
from vrusha.store.query import retrieve
from vrusha.verify import run_verify, summarise

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "pages")


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    root = tmp_path_factory.mktemp("vrusha")
    config = dict(DEFAULTS)
    config = {key: (dict(value) if isinstance(value, dict) else value)
              for key, value in DEFAULTS.items()}
    config["paths"]["root"] = str(root)
    ensure_dirs(config)
    return config


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture(scope="module")
def store(cfg, rules, registry):
    db = Store(resolve(cfg, "db"))
    report = ingest_paths([FIXTURES], db, cfg, rules, registry, HashEmbedder(256),
                          licence="MIT")
    assert report.stored >= 4, report.to_dict()
    ctx = PassContext(store=db, cfg=cfg, rules=rules, registry=registry)
    run_passes(ctx)
    yield db
    db.close()


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------

def test_licence_detection():
    assert detect_licence("Permission is hereby granted, free of charge, to any person") == "MIT"
    assert detect_licence("Apache License, Version 2.0") == "Apache-2.0"
    assert detect_licence("GNU AFFERO GENERAL PUBLIC LICENSE") == "AGPL-3.0"
    assert detect_licence("SPDX-License-Identifier: MPL-2.0") == "MPL-2.0"
    assert detect_licence("all rights reserved, proprietary") is None


def test_secret_detection_finds_key_shapes():
    text = "token = 'ghp_" + "a" * 36 + "'"
    found = find_secrets(text, patterns=[r"ghp_[A-Za-z0-9]{36}"])
    assert found


def test_minhash_is_stable_and_near_duplicates_score_high():
    text = "the quick brown fox jumps over the lazy dog " * 5
    same = minhash_signature(text)
    assert estimated_jaccard(same, minhash_signature(text)) == 1.0
    other = minhash_signature("completely different words appear in this sentence " * 5)
    assert estimated_jaccard(same, other) < 0.5
    assert band_keys(same)


def test_refused_domain_is_blocked(rules, registry):
    report = evaluate(text="word " * 300, url="https://stackoverflow.com/q/1",
                      licence="MIT", raw_chars=2000, rules=rules, registry=registry)
    assert not report.passed
    assert any(r.gate == "policy" for r in report.blocked_by)


def test_blocked_licence_is_blocked(rules, registry):
    report = evaluate(text="word " * 300, url="https://example.org/x",
                      licence="GPL-3.0", raw_chars=2000, rules=rules, registry=registry)
    assert not report.passed
    assert any(r.gate == "licence" for r in report.blocked_by)


def test_short_page_is_rejected(rules, registry):
    report = evaluate(text="too short to matter", url="https://example.org/x",
                      licence="MIT", raw_chars=100, rules=rules, registry=registry)
    assert not report.passed


# --------------------------------------------------------------------------
# chunking
# --------------------------------------------------------------------------

def test_python_chunks_on_real_boundaries(registry):
    source = (
        "import os\n\n"
        "def alpha():\n    return 1\n\n\n"
        "class Beta:\n    def gamma(self):\n        return 2\n\n\n"
        "def delta():\n    return 3\n"
    )
    chunks = chunk_text(source, registry.get("python"), DEFAULTS)
    kinds = {(c.kind, c.symbol) for c in chunks}
    assert ("function", "alpha") in kinds
    assert ("class", "Beta") in kinds
    assert ("function", "delta") in kinds


def test_prose_chunks_do_not_exceed_the_cap(registry):
    text = "\n\n".join("Sentence number %d is here and it has some length to it." % i
                       for i in range(200))
    chunks = chunk_text(text, registry.get("en"), DEFAULTS)
    assert chunks
    assert all(c.chars <= DEFAULTS["store"]["max_chunk_chars"] + 200 for c in chunks)


def test_empty_text_produces_no_chunks(registry):
    assert chunk_text("", registry.get("en"), DEFAULTS) == []
    assert chunk_text("   \n  ", registry.get("en"), DEFAULTS) == []


# --------------------------------------------------------------------------
# store and passes
# --------------------------------------------------------------------------

def test_documents_and_chunks_are_stored(store):
    stats = store.stats()
    assert stats["documents"] >= 4
    assert stats["chunks"] >= 4
    assert stats["addressed"] == stats["chunks"]


def test_every_chunk_has_an_address(store):
    rows = store.all_chunks()
    assert rows
    for row in rows:
        assert row["address"], row["chunk_id"]
        assert row["addr_domain"] in (1, 2, 3, 4, 5)


def test_code_lands_in_the_code_domain(store):
    rows = store.conn.execute(
        "SELECT address, addr_domain FROM chunks WHERE kind='function' LIMIT 5").fetchall()
    assert rows
    for row in rows:
        assert row["addr_domain"] == 2


def test_addresses_are_unique(store):
    addresses = [address for _id, address in store.addresses()]
    assert len(addresses) == len(set(addresses))


def test_passes_are_logged(store):
    history = store.pass_history(limit=50)
    ids = {entry["pass_id"] for entry in history}
    assert "assign.address" in ids
    assert "analyse.graph" in ids


def test_graph_edges_exist(store):
    assert store.edge_count() > 0


def test_verify_passes_on_a_fresh_store(store):
    results = run_verify(store)
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------

def test_retrieval_finds_the_right_page(store):
    retrieval = retrieve(store, HashEmbedder(256),
                         "why fuse keyword and vector search by rank", DEFAULTS, top_k=3)
    assert retrieval.hits
    top = retrieval.hits[0]
    assert "retrieval-augmented-generation" in top.url


def test_retrieval_finds_a_code_unit(store):
    retrieval = retrieve(store, HashEmbedder(256),
                         "BoundedQueue put when the queue is full", DEFAULTS, top_k=5)
    urls = [hit.url for hit in retrieval.hits]
    assert any("queue.py" in url for url in urls)


def test_address_hint_biases_retrieval(store):
    from vrusha.store.address import Address
    retrieval = retrieve(store, HashEmbedder(256), "encoding", DEFAULTS,
                         address_hint=Address.parse("2"), top_k=5)
    assert retrieval.hits
    code_hits = sum(1 for hit in retrieval.hits if hit.address and hit.address.domain == "code")
    assert code_hits >= 1


def test_empty_question_retrieves_nothing(store):
    retrieval = retrieve(store, HashEmbedder(256), "zzzzqqqq", DEFAULTS, top_k=3)
    assert retrieval.hits == []


# --------------------------------------------------------------------------
# html
# --------------------------------------------------------------------------

def test_html_extraction_drops_navigation():
    html = ("<html><head><title>T</title></head><body>"
            "<nav>menu menu</nav><article><p>Real content here.</p></article>"
            "<footer>footer</footer></body></html>")
    title, text, _links = html_to_text(html)
    assert title == "T"
    assert "Real content here." in text
    assert "menu" not in text
    assert "footer" not in text


def test_local_harvest_marks_status_ok():
    docs = harvest_local(FIXTURES)
    assert docs
    assert all(doc.status == 200 for doc in docs)
    assert any(doc.kind == "code" for doc in docs)
