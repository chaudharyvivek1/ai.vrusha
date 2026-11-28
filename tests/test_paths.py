"""Multi-hop paths: the shortest chain, and every hop with its evidence."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import edges, paths

TEXT = ("Acme Corporation acquired Beta Limited. "
        "Beta Limited supplies Gamma Industries. "
        "Gamma Industries partnered with Delta Systems. "
        "Dr. Meera Rao joined the Reserve Bank of India.")


def _store(tmp_path):
    from vrusha.store.db import Store
    store = Store(str(tmp_path / "paths.db"))
    edges.store_edges(store, edges.extract(TEXT), doc_id=1)
    return store


def test_one_hop_is_direct(tmp_path):
    store = _store(tmp_path)
    found = paths.find(store, "Acme Corporation", "Beta Limited")
    assert found["hops"] == 1
    assert found["path"][0]["predicate"] == "acquired"
    store.close()


def test_two_hops_through_an_intermediate(tmp_path):
    store = _store(tmp_path)
    found = paths.find(store, "Acme Corporation", "Gamma Industries", max_hops=2)
    assert found["hops"] == 2
    assert [hop["predicate"] for hop in found["path"]] == ["acquired", "supplies"]
    store.close()


def test_the_hop_cap_is_a_hard_limit(tmp_path):
    """Two hops is a plausible claim; five is 'everything is connected'."""
    store = _store(tmp_path)
    assert paths.find(store, "Acme Corporation", "Delta Systems", max_hops=2) is None
    assert paths.find(store, "Acme Corporation", "Delta Systems", max_hops=3) is not None
    store.close()


def test_every_hop_carries_its_evidence(tmp_path):
    store = _store(tmp_path)
    found = paths.find(store, "Acme Corporation", "Gamma Industries", max_hops=2)
    for hop in found["path"]:
        assert hop["sentence"], "a hop without its sentence cannot be checked"
    store.close()


def test_the_path_confidence_is_the_weakest_hop(tmp_path):
    store = _store(tmp_path)
    found = paths.find(store, "Acme Corporation", "Gamma Industries", max_hops=2)
    assert found["confidence"] == min(hop["confidence"] for hop in found["path"])
    store.close()


def test_disconnected_entities_have_no_path(tmp_path):
    """Meera Rao and Acme Corporation are in different components."""
    store = _store(tmp_path)
    assert paths.find(store, "Meera Rao", "Acme Corporation", max_hops=4) is None
    store.close()


def test_an_unknown_name_has_no_path(tmp_path):
    store = _store(tmp_path)
    assert paths.find(store, "Nothing Ltd", "Acme Corporation") is None
    store.close()


def test_a_name_reaches_itself_in_zero_hops(tmp_path):
    store = _store(tmp_path)
    found = paths.find(store, "Acme Corporation", "Acme Corporation")
    assert found["hops"] == 0 and found["path"] == []
    store.close()


def test_expand_returns_neighbours_nearest_first(tmp_path):
    store = _store(tmp_path)
    found = paths.expand(store, ["Acme Corporation"], hops=2)
    assert found[0]["hops"] == 1
    assert [item["hops"] for item in found] == sorted(item["hops"] for item in found)
    store.close()


def test_expand_with_no_seeds_is_empty(tmp_path):
    store = _store(tmp_path)
    assert paths.expand(store, []) == []
    store.close()


def test_render_makes_a_readable_sentence(tmp_path):
    store = _store(tmp_path)
    text = paths.render(paths.find(store, "Acme Corporation", "Gamma Industries",
                                   max_hops=2))
    assert "acme corporation" in text and "supplies" in text
    assert "2 hop(s)" in text
    store.close()


def test_render_of_nothing_says_so():
    assert paths.render(None) == "no path"
