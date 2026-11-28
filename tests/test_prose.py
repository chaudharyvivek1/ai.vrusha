"""Entities from prose, and the cases the rules cannot handle.

The tests that matter are the refusals: a rule-based extractor's danger is not
that it misses an entity, it is that it confidently produces one that is not
there.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import prose


def kinds(text):
    return {entity.text: entity.kind for entity in prose.extract(text)}


# ---------------------------------------------------------------------------
# what it finds
# ---------------------------------------------------------------------------


def test_an_organisation_is_found_by_its_suffix():
    assert kinds("Acme Corporation signed the deal.")["Acme Corporation"] == "organisation"


def test_an_organisation_with_a_connector_inside_its_name():
    """`Reserve Bank of India` is one entity, not two."""
    found = kinds("The Reserve Bank of India raised the rate.")
    assert found.get("Reserve Bank of India") == "organisation"


def test_a_person_is_found_after_an_honorific():
    assert kinds("Dr. Meera Rao attended the meeting.")["Meera Rao"] == "person"


def test_a_place_is_found_after_a_preposition():
    assert kinds("The office is in Pune and the team is at Nashik.")["Pune"] == "place"


def test_several_entities_in_one_sentence():
    found = kinds("Acme Corporation signed the deal in Pune.")
    assert found == {"Acme Corporation": "organisation", "Pune": "place"}


# ---------------------------------------------------------------------------
# what it refuses
# ---------------------------------------------------------------------------


def test_a_sentence_initial_capital_is_not_an_entity():
    """`He said the plan would work` has no entities, only a capital letter."""
    assert prose.extract("He said the plan would work.") == []


def test_a_run_does_not_cross_a_sentence_break():
    """`Pune. Dr Meera Rao` is two entities, not one — found by running it."""
    found = kinds("The office is in Pune. Dr. Meera Rao attended.")
    assert "Pune Dr Meera Rao" not in found
    assert found.get("Pune") == "place"


def test_lowercase_text_yields_nothing():
    assert prose.extract("acme corporation signed the deal in pune.") == []


def test_empty_text_is_not_an_error():
    assert prose.extract("") == []
    assert prose.extract(None) == []


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


def test_an_entity_carries_its_span():
    text = "Acme Corporation signed."
    entity = prose.extract(text)[0]
    assert text[entity.start:entity.end] == entity.text


def test_confidence_orders_the_kinds():
    """An organisation suffix is stronger evidence than a preposition."""
    org = prose.extract("Acme Corporation signed.")[0]
    place = prose.extract("It happened in Pune today.")[0]
    assert org.confidence > place.confidence


def test_dedupe_keeps_one_entry_per_surface():
    entities = prose.extract("Acme Corporation. Acme Corporation again.")
    assert len(entities) == 2
    assert len(prose.dedupe(entities)) == 1


def test_by_kind_counts_them():
    counts = prose.by_kind(prose.extract(
        "Acme Corporation met Beta Limited in Pune."))
    assert counts.get("organisation") == 2
    assert counts.get("place") == 1
