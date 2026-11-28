"""The address ranking: same answers as before, without the per-chunk Python.

`_address_ranking` used to parse every address string and call
`semantic_similarity` once per chunk — 64 ms of an 82 ms query at 200k chunks.
It is now one cached integer matrix and numpy. The only thing that makes that
safe is the test below: the vectorised rule must produce the **same** scores as
the function it replaced, on the same store, for the same hints.
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import query as query_module
from vrusha.store.address import Address, centroid_table, semantic_similarity
from vrusha.store.db import Store


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


def _store(tmp_path, addresses, *, centroids=()):
    store = Store(resolve(_cfg(tmp_path), "db"))
    doc = store.add_document(url="https://a.test/1", title="a", text="body " * 100)
    for address in addresses:
        chunk = store.add_chunk(doc, f"text for {address} " * 12, kind="prose")
        store.set_address(chunk, Address.parse(address))
    for domain, family, kind, cluster, vector in centroids:
        store.set_centroid(domain, family, kind, cluster,
                           np.asarray(vector, dtype=np.float32), size=10)
    store.commit()
    query_module._ADDRESS_CACHE.clear()
    return store


def _reference(store, hint, limit=50):
    """The implementation this replaced, kept here as the oracle."""
    centroids = centroid_table(store.get_centroids())
    scored = []
    for chunk_id, address in store.addresses():
        score = semantic_similarity(hint, address, centroids)
        if score > 0:
            scored.append((chunk_id, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:limit]


ADDRESSES = [
    "1.2.3.1.0", "1.2.3.1.5", "1.2.3.2.0", "1.2.4.1.0",
    "1.3.3.1.0", "2.2.3.1.0", "1.2.3.3.9",
]


@pytest.mark.parametrize("hint", ["1.2.3.1.0", "1.2.3.2.4", "1.3.3.1.0",
                                  "2.2.3.1.0", "9.9.9.9.9"])
def test_the_vectorised_ranking_matches_the_function_it_replaced(tmp_path, hint):
    store = _store(tmp_path, ADDRESSES)
    address = Address.parse(hint)
    assert query_module._address_ranking(store, address, 50) == _reference(store, address)
    store.close()


def test_centroids_still_decide_between_two_clusters(tmp_path):
    """Cluster ids are ordered by size, so only the centroid cosine can say
    whether cluster 1 and cluster 2 are about the same thing."""
    vectors = {
        1: [1.0, 0.0], 2: [0.9, 0.1], 3: [0.0, 1.0],
    }
    centroids = [(1, 2, 3, cluster, vector) for cluster, vector in vectors.items()]
    store = _store(tmp_path, ["1.2.3.1.0", "1.2.3.2.0", "1.2.3.3.0"],
                   centroids=centroids)

    address = Address.parse("1.2.3.1.0")
    ranked = query_module._address_ranking(store, address, 50)
    assert ranked == _reference(store, address)

    scores = dict(ranked)
    chunk_of = {a.segments: c for c, a in store.addresses()}
    near = chunk_of[Address.parse("1.2.3.2.0").segments]
    far = chunk_of[Address.parse("1.2.3.3.0").segments]
    # cluster 2 points the same way as cluster 1 -- the score is the dot of the
    # two centroids, 0.9, not a normalised cosine -- and cluster 3 is orthogonal
    # to it, so it scores 0 and is dropped by the `score > 0` filter
    assert near in scores and scores[near] == pytest.approx(0.9, abs=1e-6)
    assert far not in scores
    store.close()


def test_a_write_invalidates_the_cache(tmp_path):
    """The matrix is cached against a trigger-maintained revision."""
    store = _store(tmp_path, ADDRESSES)
    address = Address.parse("1.2.3.1.0")
    before = query_module._address_ranking(store, address, 50)

    doc = store.conn.execute("SELECT doc_id FROM documents LIMIT 1").fetchone()[0]
    chunk = store.add_chunk(doc, "a new chunk " * 20, kind="prose")
    store.set_address(chunk, Address.parse("1.2.3.1.1"))
    store.commit()

    after = query_module._address_ranking(store, address, 50)
    assert len(after) == len(before) + 1
    assert after == _reference(store, address)
    store.close()


def test_the_cache_is_actually_used(tmp_path, monkeypatch):
    """Five queries, one materialisation: that is the whole point of the cache."""
    store = _store(tmp_path, ADDRESSES)
    address = Address.parse("1.2.3.1.0")
    query_module._address_ranking(store, address, 50)

    calls = {"n": 0}
    real = query_module._address_table

    def counted(target_store):
        calls["n"] += 1
        return real(target_store)

    monkeypatch.setattr(query_module, "_address_table", counted)
    for _ in range(5):
        query_module._address_ranking(store, address, 50)
    assert calls["n"] == 5            # called each time...
    # ...but the table inside is the cached one: same object, no re-read
    first = query_module._ADDRESS_CACHE[str(store.path)]
    query_module._address_ranking(store, address, 50)
    assert query_module._ADDRESS_CACHE[str(store.path)] is first
    store.close()


def test_an_empty_store_ranks_nothing(tmp_path):
    store = _store(tmp_path, [])
    assert query_module._address_ranking(store, Address.parse("1.2.3.1.0"), 10) == []
    store.close()


def test_ordinals_only_matter_inside_one_cluster(tmp_path):
    """Both ordinals must be non-zero: a hint at position 0 has no position."""
    store = _store(tmp_path, ["1.2.3.1.0", "1.2.3.1.4", "1.2.3.2.4"])
    address = Address.parse("1.2.3.1.1")
    ranked = query_module._address_ranking(store, address, 50)
    assert ranked == _reference(store, address)
    scores = dict(ranked)
    chunk_of = {a.segments: c for c, a in store.addresses()}
    exact = chunk_of[Address.parse("1.2.3.1.0").segments]
    offset = chunk_of[Address.parse("1.2.3.1.4").segments]
    assert scores[exact] > scores[offset], "the ordinal is a weak penalty, not a tie"
    store.close()
