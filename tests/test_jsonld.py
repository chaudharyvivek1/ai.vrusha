"""JSON-LD: the page states its own schema, so we read it instead of guessing.

The interesting failures here are not "does json.loads work". They are: a page
with one broken block and nine good ones, a nested Offer that must not become a
one-row table, an array of offers that must, and a price written as `₹4,999`
inside a field the schema says is a number.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import records
from vrusha.store.db import Store
from vrusha.store.jsonld import blocks, coerce, column_type, entities

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "tables")


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# reading the blocks
# ---------------------------------------------------------------------------


def test_blocks_skip_a_broken_block_and_keep_the_good_ones():
    html = _read("product.html")
    parsed = blocks(html)
    assert len(parsed) == 1, "the invalid block must be dropped, not fatal"
    assert parsed[0]["@type"] == "Product"


def test_blocks_tolerate_cdata_and_trailing_semicolons():
    html = ('<script type="application/ld+json">'
            '{"@type":"Thing","name":"x"};</script>')
    assert blocks(html)[0]["name"] == "x"


def test_graph_is_flattened_into_entities():
    tables = entities(_read("catalog.html"))
    kinds = {table.type: len(table.entities) for table in tables}
    assert kinds == {"Product": 2, "Organization": 1}


def test_a_single_nested_entity_is_a_column_not_a_table():
    """`Offer` inside one `Product` is `offers_price`, not a table of one row."""
    tables = entities(_read("product.html"))
    types = [table.type for table in tables]
    assert types == ["Product"]
    values = tables[0].entities[0].values
    assert values["offers_price"] == "₹4,999"
    assert values["brand_name"] == "Acme Audio"
    assert values["aggregateRating_ratingValue"] == 4.6


def test_several_nested_entities_do_become_a_table():
    html = """<script type="application/ld+json">
    {"@type":"Product","name":"p","offers":[
      {"@type":"Offer","price":"10"},{"@type":"Offer","price":"20"}]}
    </script>"""
    tables = {table.type: table for table in entities(html)}
    assert "Offer" in tables
    assert len(tables["Offer"].entities) == 2
    # and the parent still carries both, flattened with a suffix
    parent = tables["Product"].entities[0].values
    assert parent["offers_price"] == "10"
    assert parent["offers_2_price"] == "20"


def test_arrays_of_scalars_are_joined():
    html = """<script type="application/ld+json">
    {"@type":"Recipe","name":"r","recipeCuisine":["Indian","Gujarati"]}
    </script>"""
    values = entities(html)[0].entities[0].values
    assert values["recipeCuisine"] == "Indian, Gujarati"


def test_context_and_type_are_not_columns():
    values = entities(_read("product.html"))[0].entities[0].values
    assert "@context" not in values and "@type" not in values


# ---------------------------------------------------------------------------
# typing
# ---------------------------------------------------------------------------


def test_column_type_reads_a_price_string_as_money():
    kind, _unit, currency = column_type(["₹4,999", "Rs 5.5 lakh", "₹2.4 crore"])
    assert kind == "money" and currency == "INR"


def test_column_type_reads_a_date_in_any_script():
    assert column_type(["2026-11-01", "२०२६-०९-१५"])[0] == "date"


def test_column_type_native_numbers_and_booleans():
    assert column_type([1, 2, 3])[0] == "int"
    assert column_type([1.5, 2.5])[0] == "float"
    assert column_type([True, False])[0] == "bool"
    assert column_type(["Acme", "Beta"])[0] == "text"


def test_coerce_follows_the_column_type():
    assert coerce("₹4,999", "money") == 4999
    assert coerce(4999, "int") == 4999
    assert coerce("२०२६-०९-१५", "date") == "2026-09-15"
    assert coerce(None, "money") is None
    assert coerce("", "text") is None


# ---------------------------------------------------------------------------
# through the store
# ---------------------------------------------------------------------------


def test_jsonld_becomes_a_table_with_typed_rows(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    result = records.extract_path(store, os.path.join(FIXTURES, "catalog.html"),
                                 licence="MIT", verbose=False)
    assert result["tables"] == 2 and result["rows"] == 3

    tables = {t["name"]: t for t in records.list_tables(store)}
    product = tables["Product"]
    assert product["kind"] == "jsonld" and product["rows"] == 2

    rows = records.table_rows(store, product["table_id"])
    assert rows[0]["name"] == "Laptop 14"
    assert rows[0]["offers_price"] == 550000        # "Rs 5.5 lakh"
    assert rows[1]["offers_price"] == 720000        # "₹7,20,000"
    assert rows[1]["datePublished"] == "2026-09-15"  # Devanagari digits
    assert rows[0]["offers_priceCurrency"] == "INR"


def test_jsonld_and_html_tables_live_side_by_side(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    records.extract_dir(store, FIXTURES, licence="MIT", verbose=False)
    kinds = {}
    for table in records.list_tables(store):
        kinds[table["kind"]] = kinds.get(table["kind"], 0) + 1
    assert kinds.get("jsonld", 0) >= 3        # Product x2, Organization
    assert kinds.get("html-table", 0) >= 2    # plans.html, vendors.csv


def test_re_extracting_does_not_duplicate(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    path = os.path.join(FIXTURES, "catalog.html")
    first = records.extract_path(store, path, licence="MIT", verbose=False)
    second = records.extract_path(store, path, licence="MIT", verbose=False)
    assert first["tables"] == second["tables"]
    assert len(records.list_tables(store)) == second["tables"]


def test_a_page_with_no_jsonld_is_not_an_error(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    result = records.extract_path(store, os.path.join(FIXTURES, "vendors.csv"),
                                 licence="MIT", verbose=False)
    assert result["tables"] == 1


def test_jsonld_rows_are_queryable_like_any_other(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    records.extract_path(store, os.path.join(FIXTURES, "catalog.html"),
                         licence="MIT", verbose=False)
    table_id = next(t["table_id"] for t in records.list_tables(store)
                    if t["name"] == "Product")
    total = store.conn.execute(
        "SELECT SUM(json_extract(data,'$.offers_price')) FROM x_rows "
        "WHERE table_id = ?", (table_id,)).fetchone()[0]
    assert total == 550000 + 720000


def test_kind_column_is_added_to_an_older_database(tmp_path):
    """A store created before `kind` existed must migrate, not fail."""
    store = Store(resolve(_cfg(tmp_path), "db"))
    store.conn.execute("DROP TABLE IF EXISTS x_tables")
    store.conn.execute("""CREATE TABLE x_tables (
        table_id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL,
        chunk_id INTEGER, name TEXT NOT NULL, source TEXT, n_rows INTEGER NOT NULL
        DEFAULT 0, n_cols INTEGER NOT NULL DEFAULT 0, confidence REAL NOT NULL
        DEFAULT 0, verdict TEXT, extracted TEXT NOT NULL)""")
    records.ensure_schema(store)
    columns = {row[1] for row in
               store.conn.execute("PRAGMA table_info(x_tables)").fetchall()}
    assert "kind" in columns
