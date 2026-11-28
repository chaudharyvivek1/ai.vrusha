"""Running a user script against extracted rows. The boundary, stated honestly.

Tier 1 formulas (`formula.py`) cannot execute anything: they are walked against
a whitelist and interpreted. This is the other tier — real Python, written by
you, run against data that came off the web. That is a remote-code-execution
surface by construction, so what matters is not "is it safe" but **what exactly
is contained**.

What is enforced here:

    network        a network namespace with no interfaces, when the kernel
                   allows it. Verified: connect() returns ENETUNREACH
    cpu, memory    RLIMIT_CPU and RLIMIT_AS, so a loop or an allocation dies
                   instead of taking the machine with it
    processes      RLIMIT_NPROC, so a fork bomb stops at 32
    output         RLIMIT_FSIZE and file-backed stdout, so printing a gigabyte
                   cannot exhaust memory
    wall clock     a timeout that kills the whole process group
    environment    cleared to PATH, HOME and TMPDIR inside a temp directory
    the store      never passed in. The script gets rows as JSON and returns
                   JSON; it has no handle on the database
    imports        a denylist for network and process modules, in both modes

What is **not** enforced, and you should know before you run someone else's
script:

    filesystem     the script runs as you. It can read any file you can read,
                   including the database file by its path. The store handle is
                   not given to it, but the path is guessable
    the denylist   an import guard is a speed bump. `__import__`, importlib and
                   a vendored copy of a module all walk around it
    a namespace    only blocks what it blocks. Without one (older kernels,
                   restrictive containers, some CI), the script runs in your
                   process tree with only rlimits between it and the machine

So: this stops accidents, runaway loops and naive exfiltration. It is not a
defence against a determined attacker. For that, run the whole thing in a
container with no network and a read-only filesystem. The code here is written
so that a container is a deployment choice, not a rewrite.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

#: Modules a script has no business importing. In namespace mode the network is
#: already gone; this is the belt, not the boundary.
DENIED = (
    "socket", "ssl", "http", "urllib", "urllib2", "urllib3", "ftplib",
    "smtplib", "telnetlib", "xmlrpc", "requests", "httpx", "aiohttp",
    "subprocess", "multiprocessing", "ctypes", "pty", "asyncio",
)

_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")

#: The runner, written to a temp file and executed in the child. It is fixed
#: text — nothing from the user or the corpus is interpolated into it.
RUNNER = r'''
import json, os, resource, sys

def _limit(name, soft, hard=None):
    try:
        resource.setrlimit(name, (soft, hard if hard is not None else soft))
    except (ValueError, OSError):
        pass

payload_path, script_path, result_path = sys.argv[1], sys.argv[2], sys.argv[3]
settings = json.load(open(payload_path, encoding="utf-8"))["limits"]
megabyte = 1024 * 1024
_limit(resource.RLIMIT_CPU, int(settings["cpu_s"]), int(settings["cpu_s"]) + 1)
_limit(resource.RLIMIT_AS, int(settings["memory_mb"]) * megabyte)
_limit(resource.RLIMIT_FSIZE, int(settings["fsize_mb"]) * megabyte)
_limit(resource.RLIMIT_NPROC, int(settings["nproc"]))
_limit(resource.RLIMIT_CORE, 0)
_limit(resource.RLIMIT_NOFILE, 64)

def write(payload):
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, default=str)

if settings.get("guard"):
    denied = set(settings["denied"])
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
    def guarded(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in denied:
            raise ImportError("import of %r is blocked in a vrusha script" % root)
        return real_import(name, *args, **kwargs)
    try:
        __builtins__.__import__ = guarded
    except AttributeError:
        import builtins
        builtins.__import__ = guarded

try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("vrusha_user_script", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, "transform", None) or getattr(module, "main", None)
    if function is None:
        raise RuntimeError("the script must define transform(rows) or main(rows)")
    rows = json.load(open(payload_path, encoding="utf-8"))["rows"]
    output = function(rows)
    write({"ok": True, "rows": output})
except MemoryError:
    write({"ok": False, "error": "out of memory: the script exceeded the limit"})
except BaseException as exc:
    write({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
'''


@dataclass
class ScriptResult:
    ok: bool
    rows: Optional[List[dict]] = None
    error: str = ""
    isolation: str = "process"
    exit_code: Optional[int] = None
    seconds: float = 0.0
    timed_out: bool = False
    killed: str = ""                 # timeout | signal | output
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "error": self.error, "isolation": self.isolation,
                "exit_code": self.exit_code, "seconds": round(self.seconds, 3),
                "timed_out": self.timed_out, "killed": self.killed,
                "rows": len(self.rows) if self.rows is not None else 0,
                "stdout": self.stdout[-2000:], "stderr": self.stderr[-2000:]}


_PROBE: Optional[str] = None


def isolation_available() -> str:
    """`namespace` when the kernel gives us one, else `process`. Probed once.

    A user namespace plus a network namespace is what makes the network claim
    true. Without them the script still runs isolated in a process with
    rlimits, but it can reach the network, and the docstring says so.
    """
    global _PROBE
    if _PROBE is not None:
        return _PROBE
    if not shutil.which("unshare"):
        _PROBE = "process"
        return _PROBE
    try:
        probe = subprocess.run(
            ["unshare", "--user", "--map-root-user", "--net", "--fork",
             "--", sys.executable, "-c", "print('ok')"],
            capture_output=True, timeout=10)
        _PROBE = "namespace" if probe.returncode == 0 and b"ok" in probe.stdout \
            else "process"
    except (OSError, subprocess.SubprocessError):
        _PROBE = "process"
    return _PROBE


def script_hash(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:12]


def _valid_rows(rows: Any, expected: int) -> Optional[str]:
    """None when the output is usable, otherwise why it is not."""
    if not isinstance(rows, list):
        return f"transform() returned {type(rows).__name__}, not a list"
    if len(rows) != expected:
        return (f"transform() returned {len(rows)} rows for {expected} input "
                f"rows; a derived column must keep one row per input row")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return f"row {index} is {type(row).__name__}, not a dict"
        for key, value in row.items():
            if not isinstance(key, str) or not _COLUMN.match(key):
                return f"row {index} has an invalid column name {key!r}"
            if isinstance(value, (dict, list, tuple, set)):
                return (f"row {index} column {key!r} is a "
                        f"{type(value).__name__}; a cell must be a scalar")
    return None


def run_script(script_path: str, rows: Sequence[dict], *, timeout: float = 5.0,
               memory_mb: int = 512, isolation: str = "auto",
               allow_network: bool = False) -> ScriptResult:
    """Run `transform(rows)` from `script_path` in a child process.

    The child gets the rows as JSON and returns JSON. It never sees the store,
    the config or the environment it was launched from.
    """
    if not os.path.isfile(script_path):
        return ScriptResult(ok=False, error=f"no such script: {script_path}")
    level = isolation_available() if isolation == "auto" else isolation
    if level not in ("namespace", "process"):
        return ScriptResult(ok=False,
                            error=f"unknown isolation {isolation!r}; "
                                  f"use auto, namespace or process")

    workdir = tempfile.mkdtemp(prefix="vrusha-script-")
    started = time.perf_counter()
    try:
        payload_path = os.path.join(workdir, "payload.json")
        runner_path = os.path.join(workdir, "runner.py")
        result_path = os.path.join(workdir, "result.json")
        out_path = os.path.join(workdir, "stdout.txt")
        err_path = os.path.join(workdir, "stderr.txt")

        with open(payload_path, "w", encoding="utf-8") as handle:
            json.dump({"rows": list(rows),
                       "limits": {"cpu_s": max(1, int(timeout)),
                                  "memory_mb": int(memory_mb),
                                  "fsize_mb": 16, "nproc": 32,
                                  "guard": not allow_network,
                                  "denied": list(DENIED)}}, handle,
                      ensure_ascii=False, default=str)
        with open(runner_path, "w", encoding="utf-8") as handle:
            handle.write(RUNNER)

        command = [sys.executable, "-I", "-S", runner_path, payload_path,
                   os.path.abspath(script_path), result_path]
        if level == "namespace":
            command = ["unshare", "--user", "--map-root-user", "--net",
                       "--fork", "--"] + command

        env = {"PATH": "/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir,
               "PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8"}
        timed_out = False
        with open(out_path, "wb") as out_handle, open(err_path, "wb") as err_handle:
            process = subprocess.Popen(command, cwd=workdir, env=env,
                                       stdin=subprocess.DEVNULL,
                                       stdout=out_handle, stderr=err_handle,
                                       start_new_session=True)
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                exit_code = process.wait(timeout=10)

        seconds = time.perf_counter() - started
        stdout = _tail(out_path)
        stderr = _tail(err_path)

        result = ScriptResult(ok=False, isolation=level, exit_code=exit_code,
                              seconds=seconds, timed_out=timed_out,
                              stdout=stdout, stderr=stderr)
        if timed_out:
            result.killed = "timeout"
            result.error = f"killed after {timeout:g}s"
            return result

        if not os.path.exists(result_path):
            result.killed = "signal" if exit_code and exit_code < 0 else "output"
            result.error = (f"the script wrote no result (exit {exit_code}); "
                            f"{stderr.strip()[:300] or 'no stderr'}")
            return result

        try:
            with open(result_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            result.error = f"the result file is not JSON: {exc}"
            return result

        if not payload.get("ok"):
            result.error = str(payload.get("error") or "the script failed")
            return result

        problem = _valid_rows(payload.get("rows"), len(rows))
        if problem:
            result.error = problem
            return result

        result.ok = True
        result.rows = payload["rows"]
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _tail(path: str, limit: int = 2000) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def describe() -> Dict[str, object]:
    """What this machine can actually enforce, for `vrusha doctor` and the docs."""
    level = isolation_available()
    return {"isolation": level,
            "network_blocked": level == "namespace",
            "limits": ["cpu", "memory", "processes", "file size", "wall clock"],
            "not_enforced": ["filesystem read access", "a determined import escape"]}
