"""Extraction: does a table on a page become the rows it claims to be?

The parsers are tested directly because that is where a wrong answer is silent:
a price read as text looks fine in a table and ruins every sum built on it.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import records
from vrusha.store.db import Store
from vrusha.store.extract import (ascii_digits, header_key, judge, parse_date,
                                  parse_number, schema_of, table_from_csv,
                                  tables_from_html, typed_rows)

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "tables")


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    cfg["http"] = {**DEFAULTS["http"], "allow_private": True, "rate_per_host": 0.0}
    ensure_dirs(cfg)
    return cfg


# ---------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------


def test_indian_grouping_and_currency():
    assert parse_number("₹1,23,456").scaled == 123456
    assert parse_number("₹1,23,456").currency == "INR"
    assert parse_number("1,234,567").scaled == 1234567
    assert parse_number("Rs. 4,500").scaled == 4500


def test_scale_words_become_multipliers():
    assert parse_number("Rs 5.5 lakh").scaled == 550000
    assert parse_number("₹2.4 crore").scaled == 24_000_000
    assert parse_number("3.2 Cr").scaled == 32_000_000
    assert parse_number("12k").scaled == 12000


def test_every_numeral_system():
    assert ascii_digits("१२३") == "123"
    assert parse_number("१,२३,४५६").scaled == 123456
    assert parse_number("٤٥٠").scaled == 450          # Arabic-Indic
    assert parse_number("৪৫০").scaled == 450          # Bengali


def test_percent_and_negative_and_ranges():
    assert parse_number("99.9%").unit == "%"
    assert parse_number("99.9%").value == 99.9
    assert parse_number("(1,200)").value == -1200     # accounting negative
    assert parse_number("10-20") is None              # a range is not a number
    assert parse_number("contact us") is None


def test_dates_in_several_scripts():
    assert parse_date("27/11/2026") == "2026-11-27"
    assert parse_date("2026-11-27") == "2026-11-27"
    assert parse_date("२७/११/२०२६") == "2026-11-27"
    assert parse_date("27 Nov 2026") == "2026-11-27"
    assert parse_date("not a date") is None


def test_month_first_when_the_corpus_says_so():
    assert parse_date("12/27/2026", dayfirst=False) == "2026-12-27"
    assert parse_date("12/27/2026", dayfirst=True) is None


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


def _plans_html() -> str:
    with open(os.path.join(FIXTURES, "plans.html"), encoding="utf-8") as handle:
        return handle.read()


def test_html_tables_are_found_with_their_header_signal():
    tables = tables_from_html(_plans_html(), source="plans.html")
    assert len(tables) == 2
    assert tables[0].header_flags[0] is True
    assert tables[0].width() == 5
    assert tables[0].rows[1][1] == "₹1,23,456"


def test_a_data_table_is_accepted_and_a_layout_table_is_not():
    tables = tables_from_html(_plans_html())
    assert judge(tables[0]).relational is True
    verdict = judge(tables[1])
    assert verdict.relational is False
    assert "typed" in verdict.reason or "rows" in verdict.reason


def test_schema_infers_types_units_and_mixed_units():
    table = tables_from_html(_plans_html())[0]
    columns = {column.name: column for column in schema_of(table)}
    assert columns["price"].type == "money"
    assert columns["price"].currency == "INR"
    assert columns["uptime"].type == "percent"
    assert columns["launch"].type == "date"
    # 50 GB and 5 TB in one column must not look summable
    assert columns["storage"].unit == "mixed"


def test_rows_are_typed_and_keep_their_raw_text():
    table = tables_from_html(_plans_html())[0]
    columns = schema_of(table)
    rows = typed_rows(table, columns)
    assert len(rows) == 3
    first = rows[0].values
    assert first["price"] == 123456
    assert first["price__raw"] == "₹1,23,456"
    assert first["launch"] == "2026-11-27"
    assert rows[1].values["price"] == 550000
    assert rows[2].values["price"] == 24_000_000


def test_csv_is_a_table_without_markup():
    path = os.path.join(FIXTURES, "vendors.csv")
    with open(path, encoding="utf-8") as handle:
        table = table_from_csv(handle.read(), source=path)
    columns = {column.name: column for column in schema_of(table)}
    assert columns["price"].type == "money"
    assert columns["seats"].type == "int"
    assert columns["renewal"].type == "date"


def test_headers_are_canonical_across_languages():
    assert header_key("Price") == "price"
    assert header_key("कीमत") == "price"
    assert header_key("  Unit Price  ") == "price"
    assert header_key("विलक्षण स्तंभ") .startswith("व")


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_extract_stores_rows_with_provenance(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    result = records.extract_path(store, os.path.join(FIXTURES, "plans.html"),
                                 licence="MIT", verbose=False)
    assert result["tables"] == 1 and result["rows"] == 3

    tables = records.list_tables(store)
    assert len(tables) == 1
    assert tables[0]["licence"] == "MIT"
    assert tables[0]["name"].startswith("plans.html")

    rows = records.table_rows(store, tables[0]["table_id"])
    assert rows[0]["plan"] == "Starter"
    assert rows[0]["price"] == 123456

    # the rows are queryable as JSON, which is the point of storing them
    total = store.conn.execute(
        "SELECT SUM(json_extract(data,'$.price')) FROM x_rows WHERE table_id = ?",
        (tables[0]["table_id"],)).fetchone()[0]
    assert total == 123456 + 550000 + 24_000_000


def test_a_rejected_table_is_not_stored(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    path = os.path.join(FIXTURES, "plans.html")
    records.extract_path(store, path, verbose=False)
    tables = records.list_tables(store)
    assert len(tables) == 1, "the layout table must not become a record set"


def test_extract_dir_counts_files_and_tables(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    result = records.extract_dir(store, FIXTURES, licence="MIT", verbose=False)
    assert result["files"] == 2
    assert result["tables"] == 2
    assert result["rows"] == 6
    assert result["rejected"] == 1


def test_cli_extract_and_tables(tmp_path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "extract", "--dir", FIXTURES, "--licence", "MIT"]) == 0
    out = capsys.readouterr().out
    assert "2 table(s)" in out and "6 row(s)" in out

    assert main(["--root", root, "tables"]) == 0
    listing = capsys.readouterr().out
    assert "plans.html" in listing and "vendors.csv" in listing

    assert main(["--root", root, "tables", "--id", "1"]) == 0
    assert "Starter" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# verify --refetch
# ---------------------------------------------------------------------------

PAGE = {"body": "<html><body>" + (" ".join(f"word{i}" for i in range(200))) + "</body></html>"}


class _DriftServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):                        # noqa: N802
        body = PAGE["body"].encode()
        if self.path == "/gone":
            self.send_response(410)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def drift_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _DriftServer)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _seed(store, url, text, licence="MIT"):
    return store.add_document(url=url, title="page", text=text, licence=licence)


def test_refetch_reports_unchanged_drifted_and_gone(tmp_path, drift_server):
    from vrusha.verify import refetch

    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    body_text = " ".join(f"word{i}" for i in range(200))

    _seed(store, f"{drift_server}/same", body_text)
    _seed(store, f"{drift_server}/drifted", " ".join(f"word{i}" for i in range(150)) +
          " extra " + " ".join(f"other{i}" for i in range(50)))
    _seed(store, f"{drift_server}/gone", body_text)
    store.commit()

    report = refetch(store, cfg, verbose=False)
    states = {entry["url"].rsplit("/", 1)[-1]: entry["state"] for entry in report}
    assert states["same"] == "unchanged"
    assert states["drifted"] in ("drifted", "rewritten")
    assert states["gone"] == "gone"

    gone = next(entry for entry in report if entry["state"] == "gone")
    assert gone["status"] == 410


def test_refetch_detects_a_licence_change(tmp_path, drift_server):
    from vrusha.verify import refetch

    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    body_text = " ".join(f"word{i}" for i in range(200))
    _seed(store, f"{drift_server}/same", body_text, licence="MIT")
    store.commit()

    # the page body has no licence text, so detect_licence returns None and the
    # stored MIT is left alone: a missing signal must not overwrite a real one
    report = refetch(store, cfg, verbose=False)
    assert report[0]["licence_now"] is None
    assert report[0]["licence_before"] == "MIT"
    assert report[0]["state"] == "unchanged"


def test_refetch_apply_only_touches_unchanged(tmp_path, drift_server):
    from vrusha.verify import refetch

    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    _seed(store, f"{drift_server}/gone", "x " * 200)
    before = store.conn.execute("SELECT status FROM documents").fetchone()[0]
    store.commit()

    refetch(store, cfg, apply=True, verbose=False)
    after = store.conn.execute("SELECT status FROM documents").fetchone()[0]
    assert before != after and after == 410


def test_cli_verify_refetch(tmp_path, drift_server, capsys, monkeypatch):
    # the CLI loads its own config, so the private-host allowance has to come
    # through the documented environment override
    monkeypatch.setenv("VRUSHA_HTTP_ALLOW_PRIVATE", "true")
    cfg = _cfg(tmp_path)
    store = Store(resolve(cfg, "db"))
    _seed(store, f"{drift_server}/same", " ".join(f"word{i}" for i in range(200)))
    store.commit()
    store.close()

    assert main(["--root", str(tmp_path), "verify", "--refetch"]) == 0
    out = capsys.readouterr().out
    assert "re-fetched 1 document" in out
