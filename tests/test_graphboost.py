"""The graph boost: bounded, capped, and off unless asked for.

The property that matters is the bound — a graph built from prose extraction is
incomplete, so it may reorder near-ties and may not overturn a clear lexical win.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import graphboost
from vrusha.store.query import Hit


def _hit(chunk_id, score):
    return Hit(chunk_id=chunk_id, address=None, text="text", symbol="",
               kind="prose", lang="en", url=f"https://a.test/{chunk_id}",
               title="t", licence="MIT",
               scores={"primary": score, "fused": score})


def _graph_store(tmp_path):
    from vrusha.store import communities, edges
    from vrusha.store.db import Store

    store = Store(str(tmp_path / "graph.db"))
    text = ("Acme Corporation acquired Beta Limited. "
            "Beta Limited supplies Gamma Industries.")
    edges.store_edges(store, edges.extract(text), doc_id=1)
    # give the edges a chunk so the boost can map chunk -> community
    store.conn.execute("UPDATE x_edges SET chunk_id = 7")
    store.commit()
    communities.build(store)
    return store


def test_the_boost_is_off_by_default():
    from vrusha.config import DEFAULTS
    assert DEFAULTS["graph"]["boost"] is False


def test_a_hit_in_the_querys_community_moves_up(tmp_path):
    store = _graph_store(tmp_path)
    hits = [_hit(1, 1.00), _hit(7, 0.95)]
    boosted = graphboost.boost(store, "Acme Corporation", hits)
    assert [hit.chunk_id for hit in boosted] == [7, 1]
    store.close()


def test_the_boost_is_capped(tmp_path):
    """At most 15%: enough to reorder near-ties, not enough to overturn a win."""
    store = _graph_store(tmp_path)
    hits = [_hit(1, 1.00), _hit(7, 0.50)]
    boosted = graphboost.boost(store, "Acme Corporation", hits)
    assert [hit.chunk_id for hit in boosted] == [1, 7], \
        "a 50% deficit was overturned by a 15% boost"
    store.close()


def test_no_shared_community_means_no_change(tmp_path):
    store = _graph_store(tmp_path)
    hits = [_hit(1, 1.0), _hit(2, 0.9)]
    assert graphboost.boost(store, "unrelated words only", hits) == hits
    store.close()


def test_an_empty_graph_is_not_an_error(tmp_path):
    from vrusha.store.db import Store
    store = Store(str(tmp_path / "empty.db"))
    hits = [_hit(1, 1.0), _hit(2, 0.9)]
    assert graphboost.boost(store, "Acme Corporation", hits) == hits
    store.close()


def test_no_hits_is_not_an_error(tmp_path):
    store = _graph_store(tmp_path)
    assert graphboost.boost(store, "Acme Corporation", []) == []
    store.close()


def test_only_the_cap_is_boosted(tmp_path):
    store = _graph_store(tmp_path)
    hits = [_hit(7, 0.5), _hit(7, 0.4), _hit(7, 0.3)]
    notes = []
    graphboost.boost(store, "Acme Corporation", hits, max_hits=1, notes=notes)
    assert notes and "boosted 1 hit" in notes[0]
    store.close()


# ---------------------------------------------------------------------------
# the query-to-community match, which was exact and therefore dead
# ---------------------------------------------------------------------------


def test_a_query_entity_that_is_a_prefix_of_a_stored_name_still_matches(tmp_path):
    """A question says `Alpha Design Technologies`; the graph stored
    `Alpha Design Technologies Pvt Ltd`. Exact equality returned an empty set for
    every question on the real corpus, so the boost could not move a single rank."""
    from vrusha.store import graphboost
    from vrusha.store.db import Store

    store = Store(str(tmp_path / "boost.db"))
    try:
        graphboost._ensure(store) if hasattr(graphboost, "_ensure") else None
        store.conn.execute("CREATE TABLE IF NOT EXISTS x_communities "
                           "(name TEXT, community INTEGER)")
        store.conn.execute("INSERT INTO x_communities VALUES (?, ?)",
                           ("alpha design technologies pvt ltd", 0))
        store.commit()
        found = graphboost._query_communities(
            store, "which company acquired Alpha Design Technologies")
        assert found == {0}, "a prefix of a stored name did not match"
    finally:
        store.close()


def test_an_unrelated_query_still_matches_nothing(tmp_path):
    from vrusha.store import graphboost
    from vrusha.store.db import Store

    store = Store(str(tmp_path / "none.db"))
    try:
        store.conn.execute("CREATE TABLE IF NOT EXISTS x_communities "
                           "(name TEXT, community INTEGER)")
        store.conn.execute("INSERT INTO x_communities VALUES (?, ?)",
                           ("alpha design technologies pvt ltd", 0))
        store.commit()
        assert graphboost._query_communities(store, "what is a licence gate") == set()
    finally:
        store.close()


def test_the_boost_does_not_invent_hits():
    """It may reorder; it may never add."""
    from vrusha.store import graphboost
    assert graphboost.boost(None, "anything", []) == []
