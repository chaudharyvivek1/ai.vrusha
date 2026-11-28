"""Relations: which columns across tables are one entity, and the false positives.

The dangerous output here is a wrong join, not a missing one: a join between two
tables that are not related produces a merged row that looks like data. So half
these tests are about what must *not* be joined.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import records, relations
from vrusha.store.db import Store

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "tables")


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


def _store_two(store, left_html, right_html, *, left_url="https://a.test/1",
               right_url="https://b.test/2"):
    for url, html in ((left_url, left_html), (right_url, right_html)):
        doc_id = store.add_document(url=url, title=url, text="body " * 200)
        records.extract_document(store, doc_id, html, url)


# ---------------------------------------------------------------------------
# what counts as a key
# ---------------------------------------------------------------------------


def test_an_identifier_column_is_a_key(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    doc = store.add_document(url="https://a.test/1", title="a", text="body " * 200)
    records.extract_document(store, doc, _table(
        ["sku", "price"], [["SH-100", "Rs 4,999"], ["SH-200", "Rs 5,999"]]),
        "https://a.test/1")

    table_id = records.list_tables(store)[0]["table_id"]
    columns = [c.column for c in relations.key_columns(store, table_id)]
    assert "sku" in columns
    assert "price" not in columns, "a unique number is a measure, not an identity"


def test_a_table_of_only_measures_has_no_keys(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    doc = store.add_document(url="https://a.test/1", title="a", text="body " * 200)
    records.extract_document(store, doc, _table(
        ["price", "uptime"], [["Rs 4,999", "99.9%"], ["Rs 5,999", "99.95%"]]),
        "https://a.test/1")
    table_id = records.list_tables(store)[0]["table_id"]
    assert relations.key_columns(store, table_id) == []


def test_a_column_with_duplicates_is_not_a_key(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    doc = store.add_document(url="https://a.test/1", title="a", text="body " * 200)
    records.extract_document(store, doc, _table(
        ["vendor", "price"],
        [["Acme", "Rs 100"], ["Acme", "Rs 200"], ["Acme", "Rs 300"]]),
        "https://a.test/1")
    table_id = records.list_tables(store)[0]["table_id"]
    assert "vendor" not in [c.column for c in relations.key_columns(store, table_id)]


def test_normalisation_folds_case_spacing_and_digits():
    assert relations.normalise("SKU-100") == relations.normalise("sku 100")
    assert relations.normalise("१००") == "100"
    assert relations.normalise(None) == ""
    assert relations.normalise("  Acme  Ltd ") == "acme ltd"


# ---------------------------------------------------------------------------
# which pairs join
# ---------------------------------------------------------------------------


def test_two_tables_sharing_a_key_join_with_their_evidence(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    _store_two(store,
               _table(["plan", "price"], [["Starter", "Rs 100"], ["Growth", "Rs 200"]]),
               _table(["plan", "uptime"], [["Starter", "99.9%"], ["Growth", "99.95%"]]))
    joins = relations.infer_joins(store)
    assert len(joins) == 1
    join = joins[0]
    assert join.shared == 2
    assert join.overlap == pytest.approx(1.0)
    assert join.kind == "same-column"
    assert {join.left.column, join.right.column} == {"plan"}


def test_a_partial_overlap_is_reported_with_its_number(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    # a third, numeric column so the table judge accepts it: two text columns
    # alone are rejected as "no typed column" before relations ever see them
    _store_two(store,
               _table(["sku", "name", "price"],
                      [["A", "x", "Rs 100"], ["B", "y", "Rs 200"],
                       ["C", "z", "Rs 300"]]),
               _table(["sku", "name", "price"],
                      [["A", "p", "Rs 400"], ["B", "q", "Rs 500"],
                       ["Z", "r", "Rs 600"]]))
    joins = relations.infer_joins(store, min_overlap=0.4)
    assert len(joins) == 1
    # 2 shared of 4 union: a real number, not a claim of identity
    assert joins[0].shared == 2
    assert joins[0].overlap == pytest.approx(0.5)


def test_unrelated_tables_do_not_join(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    _store_two(store,
               _table(["vendor", "price"], [["Acme", "Rs 1"], ["Beta", "Rs 2"]]),
               _table(["plan", "price"], [["Starter", "Rs 3"], ["Growth", "Rs 4"]]))
    assert relations.infer_joins(store) == []


def test_two_tables_of_the_same_prices_do_not_join(tmp_path):
    """The false positive that made this filter necessary."""
    store = Store(resolve(_cfg(tmp_path), "db"))
    _store_two(store,
               _table(["price", "plan"], [["Rs 100", "A"], ["Rs 200", "B"]]),
               _table(["price", "uptime"], [["Rs 100", "99.9%"], ["Rs 200", "99.95%"]]))
    joins = relations.infer_joins(store)
    assert all("price" not in (j.left.column, j.right.column) for j in joins)


def test_a_column_never_joins_to_itself_in_one_table(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    doc = store.add_document(url="https://a.test/1", title="a", text="body " * 200)
    records.extract_document(store, doc, _table(
        ["sku", "name"], [["A", "A"], ["B", "B"]]), "https://a.test/1")
    assert relations.infer_joins(store) == []


# ---------------------------------------------------------------------------
# storing and reading
# ---------------------------------------------------------------------------


def test_joins_are_stored_once_and_are_idempotent(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    _store_two(store,
               _table(["plan", "price"], [["A", "Rs 1"], ["B", "Rs 2"]]),
               _table(["plan", "price"], [["A", "Rs 3"], ["B", "Rs 4"]]))
    first = relations.build(store)
    second = relations.build(store)
    assert first["joins"] == second["joins"] == 1
    assert len(relations.list_joins(store)) == 1


def test_a_symmetric_pair_is_written_in_a_stable_order(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    _store_two(store,
               _table(["plan", "price"], [["A", "Rs 1"], ["B", "Rs 2"]]),
               _table(["plan", "price"], [["A", "Rs 3"], ["B", "Rs 4"]]))
    relations.build(store)
    rows = relations.list_joins(store)
    assert rows[0]["left_table"] < rows[0]["right_table"]


def test_list_joins_can_be_filtered_to_one_table(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    _store_two(store,
               _table(["plan", "price"], [["A", "Rs 1"], ["B", "Rs 2"]]),
               _table(["plan", "price"], [["A", "Rs 3"], ["B", "Rs 4"]]))
    relations.build(store)
    tables = records.list_tables(store)
    first_id = tables[0]["table_id"]
    assert len(relations.list_joins(store, table_id=first_id)) == 1
    assert len(relations.list_joins(store, table_id=999)) == 0


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def test_cli_joins_build_then_list(tmp_path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "extract", "--dir", FIXTURES, "--licence", "MIT"]) == 0
    capsys.readouterr()

    assert main(["--root", root, "joins", "--build"]) == 0
    assert "join(s) across" in capsys.readouterr().out

    assert main(["--root", root, "joins"]) == 0
    listing = capsys.readouterr().out
    assert "plan" in listing and "100%" in listing


def test_cli_joins_before_any_build_says_what_to_do(tmp_path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "extract", "--dir", FIXTURES, "--licence", "MIT"]) == 0
    capsys.readouterr()
    assert main(["--root", root, "joins"]) == 0
    assert "joins --build" in capsys.readouterr().out


def test_the_join_expression_is_a_usable_sql_fragment(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    _store_two(store,
               _table(["plan", "price"], [["A", "Rs 1"], ["B", "Rs 2"]]),
               _table(["plan", "price"], [["A", "Rs 3"], ["B", "Rs 4"]]))
    relations.build(store)
    expression = relations.list_joins(store)[0]["expression"]
    assert "=" in expression and "plan" in expression
