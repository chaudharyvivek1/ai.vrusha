"""Formulas: the arithmetic must work, and the escape hatches must not.

The second half matters more. A derived column is a string that arrives from a
config file and runs against values that came off a web page; the only thing
standing between that and arbitrary code is the whitelist in `formula.py`.
Every test in the refusal block is a real technique, not a decoration.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import records
from vrusha.store.db import Store
from vrusha.store.formula import (Derived, Formula, FormulaError, compile_spec)

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "tables")
COLUMNS = ["plan", "price", "storage", "uptime", "launch"]


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


# ---------------------------------------------------------------------------
# refusals — the security boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expression", [
    "__import__('os')",
    "__import__('os').system('id')",
    "open('/etc/passwd').read()",
    "eval('1+1')",
    "exec('x=1')",
    "compile('1','x','eval')",
    "globals()",
    "locals()",
    "vars()",
    "getattr(price, '__class__')",
    "price.__class__.__mro__",
    "'a'.upper()",
    "[i for i in range(3)]",
    "{k: 1 for k in range(2)}",
    "(lambda: 1)()",
    "price.__class__.__mro__[0]",
])
def test_dangerous_expressions_are_refused(expression):
    with pytest.raises(FormulaError):
        Formula.parse(expression, COLUMNS)


def test_a_dangerous_expression_is_refused_before_any_row_is_read():
    """The failure must happen at compile time, not halfway through a table."""
    spec = {"derived": [{"name": "bad", "expr": "__import__('os').system('id')"}]}
    with pytest.raises(FormulaError):
        compile_spec(spec, COLUMNS)


def test_unknown_columns_are_refused_with_the_available_ones():
    with pytest.raises(FormulaError) as error:
        Formula.parse("price / area", COLUMNS)
    assert "area" in str(error.value)
    assert "price" in str(error.value)


def test_only_whitelisted_functions_may_be_called():
    with pytest.raises(FormulaError, match="not a formula function"):
        Formula.parse("print(1)", COLUMNS)
    with pytest.raises(FormulaError, match="not a formula function"):
        Formula.parse("sorted([1,2])", COLUMNS)


def test_subscripts_and_attribute_access_are_refused():
    for expression in ("price[0]", "storage.value", "launch.year"):
        with pytest.raises(FormulaError):
            Formula.parse(expression, COLUMNS)


def test_spec_validation():
    with pytest.raises(FormulaError, match="needs a name"):
        compile_spec({"derived": [{"expr": "1+1"}]}, COLUMNS)
    with pytest.raises(FormulaError, match="duplicate"):
        compile_spec({"derived": [{"name": "a", "expr": "1"},
                                  {"name": "a", "expr": "2"}]}, COLUMNS)
    with pytest.raises(FormulaError, match="alphanumeric"):
        compile_spec({"derived": [{"name": "a b", "expr": "1"}]}, COLUMNS)
    with pytest.raises(FormulaError):
        compile_spec({"derived": []}, COLUMNS)


# ---------------------------------------------------------------------------
# arithmetic
# ---------------------------------------------------------------------------


def test_arithmetic_and_precedence():
    formula = Formula.parse("price / 100000 + 1", COLUMNS)
    assert formula.evaluate({"price": 200000}) == 3.0
    assert Formula.parse("(price + 1) * 2", COLUMNS).evaluate({"price": 1}) == 4
    assert Formula.parse("-price", COLUMNS).evaluate({"price": 5}) == -5
    assert Formula.parse("price ** 2", COLUMNS).evaluate({"price": 3}) == 9


def test_null_and_zero_division_become_null_not_an_exception():
    formula = Formula.parse("price / storage", COLUMNS)
    assert formula.evaluate({"price": 10, "storage": None}) is None
    assert formula.evaluate({"price": 10, "storage": 0}) is None
    assert formula.evaluate({"price": 10, "storage": 2}) == 5.0


def test_comparisons_and_conditionals():
    formula = Formula.parse("'cheap' if price < 1000 else 'dear'", COLUMNS)
    assert formula.evaluate({"price": 10}) == "cheap"
    assert formula.evaluate({"price": 5000}) == "dear"
    assert Formula.parse("price > 0 and storage > 0", COLUMNS).evaluate(
        {"price": 1, "storage": 1}) is True


def test_functions():
    assert Formula.parse("round(price / 3, 2)", COLUMNS).evaluate({"price": 10}) == 3.33
    assert Formula.parse("min(price, storage)", COLUMNS).evaluate(
        {"price": 5, "storage": 9}) == 5
    assert Formula.parse("if_null(storage, 0)", COLUMNS).evaluate(
        {"storage": None}) == 0
    assert Formula.parse("len(plan)", COLUMNS).evaluate({"plan": "Starter"}) == 7
    assert Formula.parse("abs(price)", COLUMNS).evaluate({"price": -4}) == 4


def test_date_diff():
    formula = Formula.parse("date_diff(launch, '2026-01-01')", COLUMNS)
    assert formula.evaluate({"launch": "2026-01-11"}) == 10.0
    assert formula.evaluate({"launch": None}) is None
    months = Formula.parse("date_diff(launch, '2026-01-01', 'months')", COLUMNS)
    assert months.evaluate({"launch": "2026-04-01"}) == pytest.approx(3.0, abs=0.2)


def test_guards_skip_rows_instead_of_producing_infinity():
    derived = Derived(name="per_unit", expr="price / storage",
                      guards=["storage > 0"]).compile(COLUMNS)
    assert derived.compute({"price": 100, "storage": 0}) is None
    assert derived.compute({"price": 100, "storage": 4}) == 25.0


# ---------------------------------------------------------------------------
# through the store
# ---------------------------------------------------------------------------


def _extracted(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    records.extract_path(store, os.path.join(FIXTURES, "plans.html"),
                         licence="MIT", verbose=False)
    table_id = records.list_tables(store)[0]["table_id"]
    return store, table_id


def test_derive_writes_values_and_a_column(tmp_path):
    store, table_id = _extracted(tmp_path)
    result = records.derive_table(store, table_id, {"derived": [
        {"name": "price_lakh", "expr": "price / 100000", "type": "float"},
        {"name": "is_cheap", "expr": "price < 600000", "type": "text"},
    ]})
    assert result["rows"] == 3

    rows = records.table_rows(store, table_id)
    assert rows[0]["price_lakh"] == pytest.approx(1.23456)
    assert rows[2]["price_lakh"] == pytest.approx(240.0)
    assert rows[0]["is_cheap"] is True
    assert rows[2]["is_cheap"] is False

    columns = {c["name"] for c in records.list_tables(store)[0]["columns"]}
    assert {"price_lakh", "is_cheap"} <= columns

    # and it is queryable like any other column
    total = store.conn.execute(
        "SELECT SUM(json_extract(data,'$.price_lakh')) FROM x_rows WHERE table_id = ?",
        (table_id,)).fetchone()[0]
    assert total == pytest.approx(1.23456 + 5.5 + 240.0)


def test_derive_counts_nulls_from_guards(tmp_path):
    store, table_id = _extracted(tmp_path)
    # storage is mixed units, so a guard on it makes the point: no row has a
    # storage of zero here, but a guard that is false for one row yields a null
    result = records.derive_table(store, table_id, {"derived": [
        {"name": "per_unit", "expr": "price / storage",
         "guards": ["storage > 100"]}]})
    # Starter has 50 GB and Scale has 5 TB: two rows fail the guard
    assert result["columns"][0]["nulls"] == 2


def test_a_bad_spec_writes_nothing(tmp_path):
    store, table_id = _extracted(tmp_path)
    before = [dict(row) for row in records.table_rows(store, table_id)]

    with pytest.raises(FormulaError):
        records.derive_table(store, table_id, {"derived": [
            {"name": "ok", "expr": "price / 1000"},
            {"name": "bad", "expr": "__import__('os')"}]})

    after = [dict(row) for row in records.table_rows(store, table_id)]
    assert before == after
    columns = {c["name"] for c in records.list_tables(store)[0]["columns"]}
    assert "ok" not in columns


def test_derive_on_a_missing_table_is_a_formula_error(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    with pytest.raises(FormulaError, match="no columns"):
        records.derive_table(store, 999, {"derived": [{"name": "x", "expr": "1"}]})


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def test_cli_derive_with_flags(tmp_path, capsys):
    root = str(tmp_path)
    plans = os.path.join(FIXTURES, "plans.html")
    assert main(["--root", root, "extract", "--dir", plans, "--licence", "MIT"]) == 0
    capsys.readouterr()

    assert main(["--root", root, "derive", "--table", "1", "--name", "price_lakh",
                 "--expr", "price / 100000", "--guard", "price > 0"]) == 0
    out = capsys.readouterr().out
    assert "price_lakh" in out and "0 null(s) of 3" in out

    assert main(["--root", root, "tables", "--id", "1"]) == 0
    assert "price_lakh" in capsys.readouterr().out


def test_cli_derive_with_a_spec_file(tmp_path, capsys):
    root = str(tmp_path)
    plans = os.path.join(FIXTURES, "plans.html")
    assert main(["--root", root, "extract", "--dir", plans, "--licence", "MIT"]) == 0
    spec_path = tmp_path / "formulas.json"
    spec_path.write_text(json.dumps({"derived": [
        {"name": "annual", "expr": "price * 12", "type": "float"}]}), encoding="utf-8")

    assert main(["--root", root, "derive", "--table", "1",
                 "--spec", str(spec_path)]) == 0
    assert "annual" in capsys.readouterr().out


def test_cli_refuses_a_dangerous_formula_with_exit_1(tmp_path, capsys):
    root = str(tmp_path)
    plans = os.path.join(FIXTURES, "plans.html")
    assert main(["--root", root, "extract", "--dir", plans, "--licence", "MIT"]) == 0
    capsys.readouterr()

    code = main(["--root", root, "derive", "--table", "1", "--name", "x",
                 "--expr", "__import__('os').system('id')"])
    assert code == 1
    out = capsys.readouterr().out
    assert "derive:" in out
    assert "not allowed" in out or "only named functions" in out
