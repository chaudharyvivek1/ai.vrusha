"""PDF: a page of glyphs becomes a table only when the grid says it is one.

A PDF has no markup, so every table here is an inference. These tests are as
much about the refusals as the successes: a page of prose that happens to align
must not become five rows of nonsense, and a file that will not parse must
return nothing rather than raise.
"""
import os
import sys
import zlib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import records
from vrusha.store.db import Store
from vrusha.store.pdf import (extract_text_items, grid_from_items,
                              items_from_stream, tables_from_pdf)

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "tables")
PLANS = os.path.join(FIXTURES, "plans.pdf")


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


def make_pdf(path: str, rows, *, compress: bool = True, prose: bool = True,
             x_positions=(72, 250, 400), y_start: float = 700) -> str:
    """A minimal but valid PDF: one page, one content stream, real objects.

    Written by hand rather than by a library so the fixture is readable and so
    the test suite needs no PDF dependency at all.
    """
    parts = []
    y = y_start
    for row in rows:
        for x, cell in zip(x_positions, row):
            parts.append(f"BT /F1 10 Tf {x} {y} Td ({cell}) Tj ET")
        y -= 20
    if prose:
        parts.append(f"BT /F1 10 Tf 72 {y - 80} Td "
                     f"(This paragraph is not a table and must not become one.) Tj ET")
    content = "\n".join(parts).encode("latin-1")
    if compress:
        data, entry = zlib.compress(content), "/Filter /FlateDecode "
    else:
        data, entry = content, ""

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< {entry}/Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref}\n%%EOF\n").encode()
    with open(path, "wb") as handle:
        handle.write(bytes(out))
    return path


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------


def test_the_fixture_parses_into_positioned_text():
    with open(PLANS, "rb") as handle:
        items = extract_text_items(handle.read())
    assert len(items) == 13                     # 12 cells + one prose line
    first = items[0]
    assert first.text == "Plan" and first.x == 72 and first.y == 700


def test_a_flate_compressed_stream_is_inflated(tmp_path):
    path = make_pdf(str(tmp_path / "c.pdf"),
                    [("A", "B"), ("1", "2")], compress=True)
    with open(path, "rb") as handle:
        items = extract_text_items(handle.read())
    assert [item.text for item in items][:4] == ["A", "B", "1", "2"]


def test_an_uncompressed_stream_is_read_too(tmp_path):
    path = make_pdf(str(tmp_path / "u.pdf"), [("A", "B"), ("1", "2")],
                    compress=False, prose=False)
    with open(path, "rb") as handle:
        assert len(extract_text_items(handle.read())) == 4


def test_strings_with_escapes_and_nested_parentheses():
    stream = b"BT /F1 9 Tf 10 20 Td (a \\(b\\) c) Tj ET"
    items = items_from_stream(stream)
    assert items[0].text == "a (b) c"


def test_tj_arrays_join_and_a_wide_kern_becomes_a_space():
    stream = b"BT /F1 9 Tf 10 20 Td [(Plan) -300 (A)] TJ ET"
    assert items_from_stream(stream)[0].text == "Plan A"


def test_a_file_that_is_not_a_pdf_returns_nothing():
    assert extract_text_items(b"not a pdf at all") == []
    assert tables_from_pdf(b"%PDF-1.4 garbage") == []


# ---------------------------------------------------------------------------
# the grid
# ---------------------------------------------------------------------------


def test_rows_come_out_top_down_not_upside_down():
    """A PDF's y axis points up; the header is the highest y, so it comes first."""
    with open(PLANS, "rb") as handle:
        tables = tables_from_pdf(handle.read(), "plans.pdf")
    assert len(tables) == 1
    assert tables[0].rows[0] == ["Plan", "Price", "Uptime"]
    assert tables[0].rows[1] == ["Starter", "Rs 1,23,456", "99.9%"]


def test_prose_below_the_table_does_not_become_rows():
    with open(PLANS, "rb") as handle:
        tables = tables_from_pdf(handle.read())
    rows = tables[0].rows
    assert len(rows) == 4                       # header + 3, not the paragraph
    assert not any("renew annually" in cell for row in rows for cell in row)


def test_a_single_column_of_text_is_refused():
    from vrusha.store.pdf import Item
    items = [Item(x=72, y=700 - 20 * index, size=10, text=f"line {index}")
             for index in range(6)]
    grid = grid_from_items(items)
    assert grid.rows == []
    assert "column" in grid.reason


def test_a_sparse_grid_is_refused():
    from vrusha.store.pdf import Item
    # two rows, two columns, but only two of four positions have text
    items = [Item(x=72, y=700, size=10, text="a"),
             Item(x=72, y=680, size=10, text="c")]
    grid = grid_from_items(items)
    assert not grid.rows
    assert grid.reason, "a refusal must say why"


def test_two_runs_inside_one_column_are_joined():
    """A cell is often drawn as two runs: `Rs` then the number beside it."""
    from vrusha.store.pdf import Item
    items = [Item(x=72, y=700, size=10, text="Rs"),
             Item(x=78, y=700, size=10, text="4,999"),
             Item(x=250, y=700, size=10, text="INR"),
             Item(x=72, y=680, size=10, text="x"),
             Item(x=250, y=680, size=10, text="y")]
    grid = grid_from_items(items)
    assert grid.rows[0][0] == "Rs 4,999"


def test_runs_further_apart_than_the_tolerance_are_two_columns():
    """The limit, stated: 18 points apart is a column boundary, not a cell."""
    from vrusha.store.pdf import Item
    items = [Item(x=72, y=700, size=10, text="Rs"),
             Item(x=90, y=700, size=10, text="4,999"),
             Item(x=72, y=680, size=10, text="x"),
             Item(x=250, y=680, size=10, text="y")]
    grid = grid_from_items(items)
    assert grid.rows[0][0] == "Rs"


# ---------------------------------------------------------------------------
# through the store
# ---------------------------------------------------------------------------


def test_a_pdf_becomes_a_typed_table(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    result = records.extract_path(store, PLANS, licence="MIT", verbose=False)
    assert result["tables"] == 1 and result["rows"] == 3

    table = records.list_tables(store)[0]
    assert table["name"].startswith("plans.pdf")
    columns = {c["name"]: c["type"] for c in table["columns"]}
    assert columns["price"] == "money"
    assert columns["uptime"] == "percent"

    rows = records.table_rows(store, table["table_id"])
    assert rows[0]["plan"] == "Starter"
    assert rows[0]["price"] == 123456
    assert rows[2]["price"] == 24_000_000


def test_the_pdf_text_is_stored_for_retrieval(tmp_path):
    """The table is extracted, and the prose around it still goes in the store."""
    store = Store(resolve(_cfg(tmp_path), "db"))
    records.extract_path(store, PLANS, licence="MIT", verbose=False)
    text = store.conn.execute(
        "SELECT text FROM documents WHERE url LIKE '%.pdf'").fetchone()[0]
    assert "renew annually" in text
    assert "Rs 2.4 crore" in text


def test_a_pdf_with_no_table_stores_the_document_and_no_rows(tmp_path):
    path = make_pdf(str(tmp_path / "prose.pdf"),
                    [("one long sentence that runs across the page and is not "
                      "a table at all",)], prose=False, x_positions=(72,))
    store = Store(resolve(_cfg(tmp_path), "db"))
    result = records.extract_path(store, path, licence="MIT", verbose=False)
    assert result["tables"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_cli_extract_picks_up_a_pdf(tmp_path, capsys):
    from vrusha.cli import main
    assert main(["--root", str(tmp_path), "extract", "--dir", PLANS,
                 "--licence", "MIT"]) == 0
    out = capsys.readouterr().out
    assert "1 table(s)" in out and "3 row(s)" in out
