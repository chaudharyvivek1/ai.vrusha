"""Tools: one interface for three engines, and an honest inventory.

The interesting assertions are about *refusal* and *detection*: a tool that is not
installed must say why, an action outside the whitelist must never run, and a
specification must never be reported as a capability.
"""
import json
import os
import stat
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.tools import ExternalTool, ToolResult, describe_all, get_tool, load


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


# ---------------------------------------------------------------------------
# the inventory
# ---------------------------------------------------------------------------


def test_all_three_engines_are_registered():
    names = {tool["name"] for tool in describe_all()}
    assert names == {"dubforge", "forge3d", "cutforge"}


def test_a_specification_is_never_reported_as_a_capability():
    """cutforge holds an engine boundary and open findings, not an engine."""
    cutforge = next(t for t in describe_all() if t["name"] == "cutforge")
    assert cutforge["available"] is False
    assert cutforge["mode"] == "specification"
    assert "specification only" in cutforge["reason"]


def test_an_unavailable_tool_says_what_to_install():
    tool = ExternalTool("ghost", "a tool that is not here", module="no_such_module_xyz",
                        cli="no-such-cli-xyz", actions=("ping",),
                        requires=["ghost>=1"])
    status = tool.status()
    assert status.available is False
    assert status.mode == "none"
    assert "pip install ghost" in status.reason
    assert "ghost>=1" in status.reason


def test_an_unknown_action_is_refused_before_anything_runs():
    tool = ExternalTool("x", "x", module="no_such_module_xyz", cli="no-such-cli-xyz",
                        actions=("doctor",))
    result = tool.run("rm -rf /")
    assert result.ok is False
    assert "unknown action" in result.error
    assert "doctor" in result.error


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------


def test_a_cli_on_path_is_found_and_run(tmp_path):
    cli = _script(tmp_path, "faketool",
                  "#!/usr/bin/env python3\n"
                  "import json, sys\n"
                  "print(json.dumps({'ok': True, 'argv': sys.argv[1:]}))\n")
    tool = ExternalTool("fake", "fake", module="no_such_module_xyz", cli=cli,
                        actions=("doctor",))
    assert tool.status().mode == "cli"
    result = tool.run("doctor")
    assert result.ok and result.exit_code == 0
    assert result.data["argv"][0] == "doctor"
    assert "--json" in result.data["argv"]


def test_options_become_flags_and_underscores_become_dashes(tmp_path):
    cli = _script(tmp_path, "flagtool",
                  "#!/usr/bin/env python3\n"
                  "import json, sys\n"
                  "print(json.dumps({'argv': sys.argv[1:]}))\n")
    tool = ExternalTool("fake", "fake", module="no_such_module_xyz", cli=cli,
                        actions=("dub",))
    result = tool.run("dub", input_file="a.mp4", to_lang="hi", verbose=True,
                      dry_run=False)
    argv = result.data["argv"]
    assert "--input-file" in argv and "a.mp4" in argv
    assert "--to-lang" in argv and "hi" in argv
    assert "--verbose" in argv
    assert "--dry-run" not in argv          # False is absence, not "--dry-run False"


def test_a_non_json_stdout_is_not_an_error(tmp_path):
    cli = _script(tmp_path, "plain", "#!/usr/bin/env python3\nprint('hello')\n")
    tool = ExternalTool("fake", "fake", module="no_such_module_xyz", cli=cli,
                        actions=("doctor",))
    result = tool.run("doctor")
    assert result.ok
    assert result.data is None
    assert "hello" in result.stdout


def test_a_failing_tool_reports_its_last_stderr_line(tmp_path):
    cli = _script(tmp_path, "boom",
                  "#!/usr/bin/env python3\nimport sys\n"
                  "print('one', file=sys.stderr)\nprint('the real reason', file=sys.stderr)\n"
                  "sys.exit(3)\n")
    tool = ExternalTool("fake", "fake", module="no_such_module_xyz", cli=cli,
                        actions=("doctor",))
    result = tool.run("doctor")
    assert not result.ok
    assert result.exit_code == 3
    assert "the real reason" in result.error


def test_a_tool_that_hangs_is_killed_at_the_timeout(tmp_path):
    cli = _script(tmp_path, "slow", "#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
    tool = ExternalTool("fake", "fake", module="no_such_module_xyz", cli=cli,
                        actions=("doctor",))
    result = tool.run("doctor", timeout=1)
    assert not result.ok
    assert "timed out" in result.error
    assert result.seconds < 15


def test_a_relative_checkout_resolves_against_the_repository_root():
    """Not against the current directory: a cron job runs somewhere else."""
    tool = get_tool("dubforge")
    path = tool._checkout_path()
    if path is None:
        pytest.skip("no sibling dubforge checkout on this machine")
    assert os.path.isabs(path)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def test_cli_tools_lists_the_inventory(capsys):
    assert main(["tools"]) == 0
    out = capsys.readouterr().out
    for name in ("dubforge", "forge3d", "cutforge"):
        assert name in out
    assert "specification" in out


def test_cli_tools_json_is_machine_readable(capsys):
    assert main(["--json", "tools"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {item["name"] for item in payload} == {"dubforge", "forge3d", "cutforge"}
    assert all("reason" in item for item in payload)


def test_cli_tools_unknown_name_exits_2(capsys):
    assert main(["tools", "nope"]) == 2
    assert "no tool called" in capsys.readouterr().out


def test_cli_tools_reports_a_specification_with_exit_1(capsys):
    assert main(["tools", "cutforge"]) == 1
    out = capsys.readouterr().out
    assert "specification" in out and "actions" in out
