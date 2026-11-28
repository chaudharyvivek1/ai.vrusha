"""Tools: dubforge, forge3d, cutforge — behind one interface, imported never.

Each of those is a product with its own CLI, its own dependencies and its own
tests. `vrusha tools` gives this project one way to call them, without pulling
ffmpeg, Blender or CadQuery into a package whose only hard dependency is numpy.

The decision that shapes this file: **the adapter lives here, the code does not.**
Copying 83 files of dubforge into `vrusha/tools/dubforge/` would buy duplication
and drift — two copies of one product, one of which nobody updates. What "merged"
needs to mean is that there is one repository to clone, one command to learn, and
one place that says whether the tool is usable and why not.

So a tool is found three ways, in order:

    module    `import dubforge` works: the package is installed in this environment
    checkout  a sibling directory, e.g. `../dubforge/src/dubforge`
    cli       an executable named `dubforge` on PATH

and it is always **run as a subprocess**, never imported into this process. A tool
that calls `sys.exit`, installs a signal handler or writes to a global config is
then a tool that cannot take the caller down with it.

Actions are whitelisted. `run()` does not take a command line; it takes an action
name and options, and refuses anything not in the tool's `actions` — because a
generic "run this argv" wrapper is a remote-code-execution surface with a nicer
name.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

#: A tool may not run forever. Video and 3D work is slow, so this is generous.
DEFAULT_TIMEOUT = 900.0


@dataclass
class ToolResult:
    tool: str
    action: str
    ok: bool
    exit_code: Optional[int] = None
    data: Optional[dict] = None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {"tool": self.tool, "action": self.action, "ok": self.ok,
                "exit_code": self.exit_code, "seconds": round(self.seconds, 2),
                "error": self.error, "data": self.data,
                "stdout": self.stdout[-2000:], "stderr": self.stderr[-2000:]}


@dataclass
class ToolStatus:
    name: str
    available: bool
    mode: str = ""              # module | checkout | cli | none
    reason: str = ""
    requires: List[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "available": self.available, "mode": self.mode,
                "reason": self.reason, "requires": self.requires,
                "summary": self.summary}


class ExternalTool:
    """One product, called as a subprocess, with its actions whitelisted."""

    def __init__(self, name: str, summary: str, *, module: str,
                 cli: str, actions: Sequence[str],
                 checkout: Optional[str] = None,
                 requires: Sequence[str] = (),
                 json_flag: str = "--json",
                 timeout: float = DEFAULT_TIMEOUT):
        self.name = name
        self.summary = summary
        self.module = module
        self.cli = cli
        self.actions = tuple(actions)
        self.checkout = checkout
        self.requires = list(requires)
        self.json_flag = json_flag
        self.timeout = timeout

    # -- where it is ------------------------------------------------------

    def _checkout_path(self) -> Optional[str]:
        """A sibling checkout: `<parent>/<name>/src/<module>`.

        A relative path is resolved against the repository root, not the current
        directory: a tool must be found the same way whether you ran `vrusha`
        from the repo, from `/tmp`, or from a cron job.
        """
        if not self.checkout:
            return None
        candidate = os.path.expanduser(self.checkout)
        if not os.path.isabs(candidate):
            candidate = os.path.join(_repo_root(), candidate)
        candidate = os.path.abspath(candidate)
        if os.path.isdir(os.path.join(candidate, self.module)):
            return candidate
        if os.path.isdir(os.path.join(candidate, "src", self.module)):
            return os.path.join(candidate, "src")
        return None

    def status(self) -> ToolStatus:
        try:
            import importlib.util
            if importlib.util.find_spec(self.module) is not None:
                return ToolStatus(self.name, True, "module",
                                  f"`import {self.module}` works in this environment",
                                  self.requires, self.summary)
        except (ImportError, ValueError):
            pass

        path = self._checkout_path()
        if path:
            return ToolStatus(self.name, True, "checkout",
                              f"found at {path}", self.requires, self.summary)

        found = shutil.which(self.cli)
        if found:
            return ToolStatus(self.name, True, "cli", f"found at {found}",
                              self.requires, self.summary)

        return ToolStatus(self.name, False, "none",
                          f"not installed: `pip install {self.name}` (or put "
                          f"`{self.cli}` on PATH); needs {', '.join(self.requires) or 'nothing'}",
                          self.requires, self.summary)

    # -- running it -------------------------------------------------------

    def _command(self, status: ToolStatus, action: str, options: dict) -> List[str]:
        if status.mode == "module":
            base = [sys.executable, "-m", self.module]
        elif status.mode == "checkout":
            base = [sys.executable, "-m", self.module]
        else:
            base = [status.reason.rsplit("found at ", 1)[-1].strip()]
        command = base + [action]
        if self.json_flag and options.pop("json", True):
            command.append(self.json_flag)
        for key, value in options.items():
            flag = f"--{key.replace('_', '-')}"
            if value is True:
                command.append(flag)
            elif value is not None and value is not False:
                command.extend([flag, str(value)])
        return command

    def run(self, action: str, *, timeout: Optional[float] = None,
            **options) -> ToolResult:
        import time

        if action not in self.actions:
            return ToolResult(self.name, action, False,
                              error=f"unknown action {action!r}; this tool allows "
                                    f"{', '.join(self.actions)}")
        status = self.status()
        if not status.available:
            return ToolResult(self.name, action, False, error=status.reason)

        command = self._command(status, action, dict(options))
        env = dict(os.environ)
        path = self._checkout_path()
        if status.mode == "checkout" and path:
            env["PYTHONPATH"] = os.pathsep.join(
                [path] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))

        started = time.perf_counter()
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=timeout or self.timeout, env=env,
                                       stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return ToolResult(self.name, action, False,
                              seconds=time.perf_counter() - started,
                              error=f"timed out after {timeout or self.timeout:g}s")
        except OSError as exc:
            return ToolResult(self.name, action, False,
                              error=f"could not start {self.cli}: {exc}")

        seconds = time.perf_counter() - started
        result = ToolResult(self.name, action, completed.returncode == 0,
                            exit_code=completed.returncode,
                            stdout=completed.stdout, stderr=completed.stderr,
                            seconds=seconds)
        if not result.ok:
            result.error = (completed.stderr.strip().splitlines() or
                            [f"exit {completed.returncode}"])[-1][:400]
        payload = completed.stdout.strip()
        if payload.startswith("{") or payload.startswith("["):
            try:
                result.data = json.loads(payload)
            except ValueError:
                result.data = None            # not JSON after all: stdout stands
        return result


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    """The directory that holds `vrusha/` — i.e. the clone this code came from."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sibling(name: str) -> str:
    """`../<name>` next to this repository, which is where a checkout usually is."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(os.path.dirname(here), name)


TOOLS: Dict[str, ExternalTool] = {}


def register(tool: ExternalTool) -> ExternalTool:
    TOOLS[tool.name] = tool
    return tool


def get_tool(name: str) -> Optional[ExternalTool]:
    if not TOOLS:
        load()
    return TOOLS.get(name)


def load() -> Dict[str, ExternalTool]:
    """Import the tool modules, so each one registers itself."""
    if TOOLS:
        return TOOLS
    from . import cutforge, dubforge, forge3d       # noqa: F401  (registration)
    return TOOLS


def describe_all() -> List[dict]:
    return [tool.status().to_dict() for tool in load().values()]
