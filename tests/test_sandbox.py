"""Running user code: the limits that are enforced, and the ones that are not.

Half these tests are about what the sandbox *stops* — a loop, an allocation, a
socket, a wrong-shaped result — because a script that runs on scraped data is a
remote-code-execution surface and the interesting question is what it can reach.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import records
from vrusha.store.db import Store
from vrusha.store.sandbox import describe, isolation_available, run_script, script_hash

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "tables")
ROWS = [{"plan": "Starter", "price": 123456}, {"plan": "Growth", "price": 550000}]


def _script(tmp_path, name: str, body: str) -> str:
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


def test_a_transform_runs_and_returns_rows(tmp_path):
    path = _script(tmp_path, "good", "def transform(rows):\n"
                                  "    return [{'lakh': (r['price'] or 0) / 100000}\n"
                                  "            for r in rows]\n")
    result = run_script(path, ROWS, timeout=10)
    assert result.ok, result.error
    assert result.rows == [{"lakh": 1.23456}, {"lakh": 5.5}]
    assert result.isolation in ("namespace", "process")


def test_main_is_accepted_as_the_entry_point(tmp_path):
    path = _script(tmp_path, "main", "def main(rows):\n"
                                    "    return [{'n': 1} for _ in rows]\n")
    assert run_script(path, ROWS, timeout=10).ok


def test_a_missing_script_file_is_refused():
    result = run_script("/nonexistent/script.py", ROWS, timeout=5)
    assert not result.ok and "no such script" in result.error


def test_the_script_gets_no_environment_and_no_store(tmp_path):
    """The child sees PATH/HOME/TMPDIR and the rows. Nothing else."""
    path = _script(tmp_path, "env", "import os\n"
                                   "def transform(rows):\n"
                                   "    leaked = os.environ.get('VRUSHA_PATHS_ROOT')\n"
                                   "    return [{'leaked': leaked} for _ in rows]\n")
    result = run_script(path, ROWS, timeout=10)
    assert result.ok, result.error
    assert result.rows[0]["leaked"] is None


# ---------------------------------------------------------------------------
# refusals: the shape of the answer
# ---------------------------------------------------------------------------


def test_a_script_without_an_entry_point_is_refused(tmp_path):
    path = _script(tmp_path, "noentry", "x = 1\n")
    result = run_script(path, ROWS, timeout=10)
    assert not result.ok and "transform(rows)" in result.error


def test_an_exception_is_surfaced_not_swallowed(tmp_path):
    path = _script(tmp_path, "boom", "def transform(rows):\n"
                                    "    raise ValueError('bad data')\n")
    result = run_script(path, ROWS, timeout=10)
    assert not result.ok and "bad data" in result.error


def test_a_wrong_number_of_rows_is_refused(tmp_path):
    path = _script(tmp_path, "short", "def transform(rows):\n"
                                     "    return [{'a': 1}]\n")
    result = run_script(path, ROWS, timeout=10)
    assert not result.ok and "one row per input row" in result.error


def test_a_nested_value_is_refused(tmp_path):
    path = _script(tmp_path, "nested", "def transform(rows):\n"
                                      "    return [{'a': {'b': 1}} for _ in rows]\n")
    result = run_script(path, ROWS, timeout=10)
    assert not result.ok and "scalar" in result.error


def test_an_invalid_column_name_is_refused(tmp_path):
    path = _script(tmp_path, "badname", "def transform(rows):\n"
                                       "    return [{'a b; drop': 1} for _ in rows]\n")
    result = run_script(path, ROWS, timeout=10)
    assert not result.ok and "invalid column name" in result.error


# ---------------------------------------------------------------------------
# refusals: the machine
# ---------------------------------------------------------------------------


def test_an_infinite_loop_is_killed_at_the_timeout(tmp_path):
    path = _script(tmp_path, "loop", "def transform(rows):\n"
                                    "    while True:\n"
                                    "        pass\n")
    result = run_script(path, ROWS, timeout=2)
    assert not result.ok
    assert result.timed_out and result.killed == "timeout"
    assert result.seconds < 15


def test_a_memory_bomb_is_stopped_by_the_limit(tmp_path):
    path = _script(tmp_path, "hog", "def transform(rows):\n"
                                   "    ballast = bytearray(2 * 1024 * 1024 * 1024)\n"
                                   "    return [{'n': len(ballast)} for _ in rows]\n")
    result = run_script(path, ROWS, timeout=20, memory_mb=256)
    assert not result.ok
    assert "memory" in result.error.lower() or result.killed in ("signal", "output")


def test_a_network_import_is_blocked(tmp_path):
    path = _script(tmp_path, "net", "def transform(rows):\n"
                                   "    import socket\n"
                                   "    socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
                                   "    return [{} for _ in rows]\n")
    result = run_script(path, ROWS, timeout=15)
    assert not result.ok
    assert "blocked" in result.error or "unreachable" in result.error


@pytest.mark.skipif(isolation_available() != "namespace",
                    reason="needs a user+network namespace")
def test_the_namespace_blocks_the_network_even_without_the_import_guard(tmp_path):
    """The guard is a speed bump; the namespace is the boundary. Prove it."""
    path = _script(tmp_path, "net2", "import socket\n"
                                    "def transform(rows):\n"
                                    "    socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
                                    "    return [{} for _ in rows]\n")
    result = run_script(path, ROWS, timeout=15, allow_network=True)
    assert not result.ok
    assert "unreachable" in result.error.lower() or "errno 101" in result.error.lower()


def test_describe_states_what_is_not_enforced():
    facts = describe()
    assert facts["isolation"] in ("namespace", "process")
    assert "filesystem read access" in facts["not_enforced"]


# ---------------------------------------------------------------------------
# through the store and the command
# ---------------------------------------------------------------------------


def test_apply_script_stamps_the_column_with_the_script_hash(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    records.extract_path(store, os.path.join(FIXTURES, "plans.html"),
                         licence="MIT", verbose=False)
    table_id = records.list_tables(store)[0]["table_id"]

    path = _script(tmp_path, "lakh", "def transform(rows):\n"
                                    "    return [{'price_lakh': (r['price'] or 0) / 100000}\n"
                                    "            for r in rows]\n")
    result = records.apply_script(store, table_id, path, timeout=15)
    assert result["ok"], result.get("error")
    assert result["origin"] == f"script:{script_hash(path)}"

    rows = records.table_rows(store, table_id)
    assert rows[0]["price_lakh"] == pytest.approx(1.23456)

    origin = store.conn.execute(
        "SELECT origin FROM x_columns WHERE table_id = ? AND name = 'price_lakh'",
        (table_id,)).fetchone()[0]
    assert origin.startswith("script:")


def test_a_failing_script_writes_nothing(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    records.extract_path(store, os.path.join(FIXTURES, "plans.html"),
                         licence="MIT", verbose=False)
    table_id = records.list_tables(store)[0]["table_id"]
    before = [dict(row) for row in records.table_rows(store, table_id)]

    path = _script(tmp_path, "bad", "def transform(rows):\n"
                                   "    raise RuntimeError('nope')\n")
    result = records.apply_script(store, table_id, path, timeout=15)
    assert not result["ok"]
    assert [dict(row) for row in records.table_rows(store, table_id)] == before


def test_cli_refuses_a_script_without_the_flag(tmp_path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "extract", "--dir", FIXTURES, "--licence", "MIT"]) == 0
    capsys.readouterr()
    path = _script(tmp_path, "ok", "def transform(rows):\n"
                                  "    return [{'x': 1} for _ in rows]\n")

    code = main(["--root", root, "derive", "--table", "1", "--script", path])
    assert code == 2
    out = capsys.readouterr().out
    assert "--allow-scripts" in out and "--expr" in out


def test_cli_runs_a_script_with_the_flag(tmp_path, capsys):
    root = str(tmp_path)
    plans = os.path.join(FIXTURES, "plans.html")
    assert main(["--root", root, "extract", "--dir", plans, "--licence", "MIT"]) == 0
    capsys.readouterr()
    path = _script(tmp_path, "ok2", "def transform(rows):\n"
                                   "    return [{'price_lakh': (r['price'] or 0) / 100000}\n"
                                   "            for r in rows]\n")

    assert main(["--root", root, "derive", "--table", "1", "--script", path,
                 "--allow-scripts", "--timeout", "20"]) == 0
    out = capsys.readouterr().out
    assert "script:" in out and "price_lakh" in out


def test_cli_reports_a_failing_script_with_exit_1(tmp_path, capsys):
    root = str(tmp_path)
    plans = os.path.join(FIXTURES, "plans.html")
    assert main(["--root", root, "extract", "--dir", plans, "--licence", "MIT"]) == 0
    capsys.readouterr()
    path = _script(tmp_path, "bad2", "def transform(rows):\n"
                                    "    raise ValueError('nope')\n")

    assert main(["--root", root, "derive", "--table", "1", "--script", path,
                 "--allow-scripts", "--timeout", "20"]) == 1
    assert "nope" in capsys.readouterr().out
