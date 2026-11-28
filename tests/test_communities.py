"""Communities: two clusters are two clusters, and the answer never changes.

The property that makes this testable is determinism — a detector that gives a
different partition on a second run cannot be cited, so it is asserted directly.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import communities


def labels_for(pairs):
    return communities.propagate(communities.adjacency(pairs))


def test_two_disjoint_triangles_are_two_communities():
    pairs = [("acme", "beta"), ("beta", "gamma"), ("gamma", "acme"),
             ("meera", "rbi"), ("rbi", "meera"), ("meera", "bank")]
    labels = labels_for(pairs)
    assert labels["acme"] == labels["beta"] == labels["gamma"]
    assert labels["meera"] == labels["rbi"] == labels["bank"]
    assert labels["acme"] != labels["meera"]


def test_a_chain_becomes_one_community():
    pairs = [("a", "b"), ("b", "c"), ("c", "d")]
    labels = labels_for(pairs)
    assert len(set(labels.values())) == 1


def test_the_answer_is_the_same_on_a_second_run():
    """Ties go to the smaller label, and nodes are visited in sorted order."""
    pairs = [("a", "b"), ("c", "d"), ("b", "c"), ("e", "f")]
    first = labels_for(pairs)
    for _ in range(5):
        assert labels_for(pairs) == first


def test_an_isolated_node_is_its_own_community():
    labels = labels_for([("a", "b")])
    labels.update({"lonely": 99})
    summary = communities.summarise(labels)
    assert summary["singletons"] == 1


def test_a_self_loop_is_ignored():
    assert communities.adjacency([("a", "a")]) == {}


def test_an_empty_graph_has_no_communities():
    assert communities.propagate({}) == {}
    assert communities.summarise({}) == {"nodes": 0, "communities": 0,
                                         "largest": 0, "singletons": 0}


def test_community_ids_are_dense_and_stable():
    pairs = [("a", "b"), ("c", "d")]
    labels = labels_for(pairs)
    assert set(labels.values()) == {0, 1}


# ---------------------------------------------------------------------------
# through the store
# ---------------------------------------------------------------------------


def _store_with_edges(tmp_path):
    from vrusha.store import edges as edges_mod
    from vrusha.store.db import Store

    store = Store(str(tmp_path / "graph.db"))
    text = ("Acme Corporation acquired Beta Limited. "
            "Beta Limited supplies Gamma Industries. "
            "Dr. Meera Rao joined the Reserve Bank of India.")
    edges_mod.store_edges(store, edges_mod.extract(text), doc_id=1)
    return store


def test_detect_and_store_round_trip(tmp_path):
    store = _store_with_edges(tmp_path)
    result = communities.build(store)
    assert result["stored"] > 0
    listed = communities.list_communities(store)
    assert listed and listed[0]["size"] >= 2
    store.close()


def test_a_name_belongs_to_the_community_its_neighbours_are_in(tmp_path):
    store = _store_with_edges(tmp_path)
    communities.build(store)
    acme = communities.community_of(store, "acme corporation")
    beta = communities.community_of(store, "beta limited")
    assert acme is not None and acme == beta
    store.close()


def test_an_unknown_name_has_no_community(tmp_path):
    store = _store_with_edges(tmp_path)
    communities.build(store)
    assert communities.community_of(store, "nothing ltd") is None
    store.close()


def test_members_are_ordered_by_degree(tmp_path):
    store = _store_with_edges(tmp_path)
    communities.build(store)
    community = communities.community_of(store, "beta limited")
    listed = communities.members(store, community)
    degrees = [row["degree"] for row in listed]
    assert degrees == sorted(degrees, reverse=True)
    store.close()
