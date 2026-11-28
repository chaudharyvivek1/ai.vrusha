"""Relations with evidence: two entities, a known verb, and the sentence.

The tests that matter are the refusals — an invented edge is a wrong answer, a
missing edge is only a gap.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import edges


def triples(text):
    return [(e.subject, e.predicate, e.object) for e in edges.extract(text)]


def test_an_acquisition_is_found():
    assert ("Acme Corporation", "acquired", "Beta Limited") in triples(
        "Acme Corporation acquired Beta Limited in March.")


def test_a_supply_relation_is_found():
    assert ("Beta Limited", "supplies", "Gamma Industries") in triples(
        "Beta Limited supplies steel to Gamma Industries.")


def test_a_person_joining_an_organisation():
    found = triples("Dr. Meera Rao joined the Reserve Bank of India.")
    assert ("Meera Rao", "works_for", "Reserve Bank of India") in found


def test_a_subsidiary_relation_is_found():
    assert ("Acme Corporation", "subsidiary_of", "Beta Limited") in triples(
        "Acme Corporation is a subsidiary of Beta Limited.")


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------


def test_every_edge_carries_the_sentence_that_asserted_it():
    edge = edges.extract("Acme Corporation acquired Beta Limited in March.")[0]
    assert edge.sentence.strip() == "Acme Corporation acquired Beta Limited in March."
    assert edge.subject in edge.sentence and edge.object in edge.sentence


def test_the_evidence_is_verbatim():
    text = "In March, Acme Corporation acquired Beta Limited."
    edge = edges.extract(text)[0]
    assert edge.sentence in text


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_a_month_is_not_an_entity_a_relation_can_hang_off():
    """`Acme acquired March` was the first false edge this module produced."""
    assert all(object_ != "March" for _s, _p, object_ in triples(
        "Acme Corporation acquired Beta Limited in March."))


def test_no_verb_means_no_edge():
    assert triples("Acme Corporation and Beta Limited are both in Pune.") == []


def test_one_entity_is_not_a_relation():
    assert edges.extract("Acme Corporation signed the deal.") == []


def test_a_verb_across_two_sentences_makes_no_edge():
    """The edge inside the second sentence is real; the cross-sentence one is not."""
    found = triples("Acme Corporation signed. Beta Limited acquired Gamma Industries.")
    assert ("Acme Corporation", "acquired", "Gamma Industries") not in found
    assert ("Beta Limited", "acquired", "Gamma Industries") in found


def test_lowercase_text_yields_nothing():
    assert edges.extract("acme corporation acquired beta limited.") == []


def test_empty_text_is_not_an_error():
    assert edges.extract("") == []


# ---------------------------------------------------------------------------
# storing
# ---------------------------------------------------------------------------


def test_edges_round_trip_through_the_store(tmp_path):
    from vrusha.store.db import Store

    store = Store(str(tmp_path / "edges.db"))
    found = edges.extract("Acme Corporation acquired Beta Limited.")
    assert edges.store_edges(store, found, doc_id=1) == 1
    stored = edges.list_edges(store)
    assert stored[0]["subject"] == "Acme Corporation"
    assert stored[0]["sentence"].startswith("Acme Corporation acquired")
    store.close()


def test_neighbours_lists_both_directions(tmp_path):
    from vrusha.store.db import Store

    store = Store(str(tmp_path / "edges.db"))
    edges.store_edges(store, edges.extract(
        "Acme Corporation acquired Beta Limited. Beta Limited supplies Gamma Industries."),
        doc_id=1)
    around = edges.neighbours(store, "Beta Limited")
    predicates = {row["predicate"] for row in around}
    assert "acquired" in predicates and "supplies" in predicates
    store.close()


def test_storing_twice_for_one_document_replaces_it(tmp_path):
    from vrusha.store.db import Store

    store = Store(str(tmp_path / "edges.db"))
    text = "Acme Corporation acquired Beta Limited."
    edges.store_edges(store, edges.extract(text), doc_id=1)
    edges.store_edges(store, edges.extract(text), doc_id=1)
    assert len(edges.list_edges(store)) == 1, "a re-extract duplicated the edges"
    store.close()
