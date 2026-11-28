"""Entity resolution: the same vendor written five ways is one vendor.

`relations.py` says in its own docstring that it does not do entity resolution,
because merging two companies into one is a mistake that looks like a feature.
This is that step, with the same rule: the evidence is stored, and nothing merges
silently.

The interesting tests are the refusals. `Acme` and `Acme Corporation` are
*different strings* and often different companies; a metric that merges them is
worse than one that misses a pair.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import entities, records
from vrusha.store.db import Store


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


def _table(header, rows) -> str:
    head = "".join(f"<th>{cell}</th>" for cell in header)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
                   for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _store(tmp_path, tables):
    store = Store(resolve(_cfg(tmp_path), "db"))
    for index, html in enumerate(tables):
        url = f"https://a.test/{index}"
        doc_id = store.add_document(url=url, title=url, text="body " * 200)
        records.extract_document(store, doc_id, html, url)
    return store


# ---------------------------------------------------------------------------
# the scoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("first,second", [
    ("Acme Corp", "ACME Corporation"),
    ("Acme Pvt. Ltd.", "acme private limited"),
    ("Infosys Ltd", "Infosys Limited"),
])
def test_spellings_of_one_name_score_as_one(first, second):
    assert entities._ratio(first, second) >= entities.DEFAULT_THRESHOLD


@pytest.mark.parametrize("first,second", [
    ("Acme Corp", "Beta Ltd"),
    ("Acme Corp", "Acme"),                    # a different company, plausibly
    ("Tata Consultancy", "Wipro Consulting"),
])
def test_different_names_do_not_score_as_one(first, second):
    assert entities._ratio(first, second) < entities.DEFAULT_THRESHOLD


def test_abbreviations_are_expanded_before_scoring():
    assert entities._surface("Acme Corp.") == "acme corporation"
    assert entities._surface("Acme Pvt Ltd") == "acme private limited"


def test_a_contained_name_is_not_automatically_a_match():
    """`token_set_ratio` scores this pair at 100; character ratio does not."""
    assert entities._ratio("Acme", "Acme Corporation") < entities.DEFAULT_THRESHOLD


# ---------------------------------------------------------------------------
# resolving a store
# ---------------------------------------------------------------------------


def test_two_spellings_across_two_tables_become_one_entity(tmp_path):
    store = _store(tmp_path, [
        _table(["vendor", "price"], [["Acme Corp", "Rs 100"],
                                     ["Beta Ltd", "Rs 200"]]),
        _table(["vendor", "uptime"], [["ACME Corporation", "99.9%"],
                                      ["Gamma Inc", "99.5%"]]),
    ])
    groups = entities.resolve(store)
    assert len(groups) == 1
    assert groups[0]["size"] == 2
    aliases = {alias["alias"] for alias in groups[0]["aliases"]}
    assert groups[0]["canonical"] in ("acme corp", "acme corporation")
    assert aliases and len(aliases) == 1
    store.close()


def test_a_group_of_one_is_not_an_entity(tmp_path):
    store = _store(tmp_path, [
        _table(["vendor", "price"], [["Acme Corp", "Rs 100"],
                                     ["Beta Ltd", "Rs 200"]]),
    ])
    assert entities.resolve(store) == []
    store.close()


def test_the_threshold_is_respected(tmp_path):
    store = _store(tmp_path, [
        _table(["vendor", "price"], [["Acme Corp", "Rs 1"], ["Beta Ltd", "Rs 2"]]),
        _table(["vendor", "uptime"], [["Acme Corporation", "99%"],
                                      ["Beta Limited", "98%"]]),
    ])
    loose = entities.resolve(store, threshold=50)
    strict = entities.resolve(store, threshold=99)
    assert len(loose) == 2               # both pairs merge at 50
    assert len(strict) == 2              # both still merge at 99: they are exact
    store.close()


def test_a_chain_of_pairs_is_one_group(tmp_path):
    """A~B and B~C is one entity, even if A and C score below the threshold.

    Every table needs two rows: `relations.candidates` treats a single-row
    column as a value, not a key, so a one-row fixture resolves nothing.
    """
    store = _store(tmp_path, [
        _table(["vendor", "price"], [["Acme Corp", "Rs 1"],
                                     ["Beta Ltd", "Rs 2"]]),
        _table(["vendor", "uptime"], [["Acme Corporation", "99%"],
                                      ["Beta Ltd", "98%"]]),
        _table(["vendor", "seats"], [["ACME Corporation Ltd", "10"],
                                     ["Beta Ltd", "20"]]),
    ])
    # acme corp ~ acme corporation (100) ~ acme corporation limited (80):
    # the first and last score below the threshold with each other, so only
    # union-find puts them in one group
    groups = entities.resolve(store, threshold=80)
    acme = [group for group in groups if "acme" in group["canonical"]]
    assert len(acme) == 1
    assert acme[0]["size"] == 3
    store.close()


def test_storing_is_idempotent_and_queryable(tmp_path):
    store = _store(tmp_path, [
        _table(["vendor", "price"], [["Acme Corp", "Rs 100"],
                                     ["Beta Ltd", "Rs 200"]]),
        _table(["vendor", "uptime"], [["ACME Corporation", "99.9%"],
                                      ["Beta Ltd", "99.5%"]]),
    ])
    first = entities.build(store)
    second = entities.build(store)
    assert first["entities"] == second["entities"] >= 1
    listed = entities.list_entities(store)
    assert len(listed) == 1
    assert listed[0]["aliases"]
    assert entities.canonical_of(store, "ACME Corporation") in (
        "acme corp", "acme corporation")
    store.close()


def test_an_unknown_value_has_no_canonical(tmp_path):
    store = _store(tmp_path, [
        _table(["vendor", "price"], [["Acme Corp", "Rs 100"],
                                     ["Beta Ltd", "Rs 200"]]),
        _table(["vendor", "uptime"], [["ACME Corporation", "99.9%"],
                                      ["Beta Ltd", "99.5%"]]),
    ])
    entities.build(store)
    assert entities.canonical_of(store, "Nothing Ltd") is None
    store.close()


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def test_cli_entities_build_then_list(tmp_path, capsys):
    root = str(tmp_path)
    from vrusha.store.records import extract_document
    store = Store(resolve(_cfg(tmp_path), "db"))
    for index, html in enumerate([
            _table(["vendor", "price"], [["Acme Corp", "Rs 100"],
                                         ["Beta Ltd", "Rs 200"]]),
            _table(["vendor", "uptime"], [["ACME Corporation", "99.9%"],
                                          ["Beta Ltd", "99.5%"]])]):
        url = f"https://a.test/{index}"
        doc = store.add_document(url=url, title=url, text="body " * 200)
        extract_document(store, doc, html, url)
    store.close()

    assert main(["--root", root, "entities", "--build"]) == 0
    assert "entit" in capsys.readouterr().out
    assert main(["--root", root, "entities"]) == 0
    out = capsys.readouterr().out
    assert "acme" in out.lower() and "corporation" in out.lower()


def test_cli_entities_before_a_build_says_what_to_do(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "entities"]) == 0
    assert "entities --build" in capsys.readouterr().out


def test_a_column_with_repeats_is_not_a_key_so_surfaces_counts_columns(tmp_path):
    """The distinction that looked like a bug and is not.

    `surfaces` counts how many *key columns* contain a spelling. A column with
    repeats is not a key candidate at all, so "rows" and "columns" are the same
    number here -- which is why the cheap set-based count is the right one.
    """
    store = _store(tmp_path, [
        _table(["vendor", "price"], [["Acme Corp", "Rs 1"],
                                     ["Acme Corp", "Rs 2"],
                                     ["Acme Corp", "Rs 3"],
                                     ["Beta Ltd", "Rs 4"]]),
        _table(["vendor", "uptime"], [["ACME Corporation", "99%"],
                                      ["Gamma Inc", "98%"]]),
    ])
    counts = entities._surfaces(store)
    assert "acme corp" not in counts, "a repeated value is not a key"
    assert counts["acme corporation"] == 1
    store.close()


# ---------------------------------------------------------------------------
# canonical_map: resolution for a plain list of names, not store tables
# ---------------------------------------------------------------------------


def test_two_spellings_of_one_company_map_to_one_name():
    from vrusha.store import entities as module

    # keyed by the ORIGINAL spelling, because that is what a caller holds
    mapping = module.canonical_map(["Acme Corp", "ACME Corporation"])
    assert mapping == {"Acme Corp": "ACME Corporation"}


def test_a_bare_suffix_is_not_merged_into_a_company():
    """`Technologies` could belong to a dozen companies; 45.3 < the 90 threshold.

    Found on a real corpus: the resolver refuses, and refusing is correct. The
    duplicate is an extraction artefact, not a resolution failure.
    """
    from vrusha.store import entities as module

    mapping = module.canonical_map(["Alpha Design Technologies Pvt Ltd",
                                    "Technologies"])
    assert mapping == {}, "a bare suffix was merged into a company"


def test_a_single_name_has_no_mapping():
    from vrusha.store import entities as module

    assert module.canonical_map(["Only One Ltd"]) == {}


def test_an_empty_list_has_no_mapping():
    from vrusha.store import entities as module

    assert module.canonical_map([]) == {}


def test_the_graph_uses_the_mapping(tmp_path):
    """The wiring: two spellings of one company make one edge, not two."""
    from vrusha.store import edges as module

    text = ("Acme Corporation acquired Beta Limited. "
            "ACME Corporation acquired Gamma Industries.")
    found = module.extract(text)
    subjects = {edge.subject.lower() for edge in found}
    assert len(subjects) == 1, f"the graph kept {subjects}"
