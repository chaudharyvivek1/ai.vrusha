"""Doctor: what this machine can do, before you blame the pipeline.

Every check is a fact with a consequence written next to it. Nothing here
changes anything; it only looks.
"""
from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import sys
from typing import Dict, List

from . import __version__
from .config import resolve
from .languages import load_registry
from .rules import load_rules


def _check(name: str, ok: bool, detail: str, consequence: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail, "consequence": consequence}


def run_doctor(cfg: dict) -> List[dict]:
    checks: List[dict] = []

    checks.append(_check(
        "python", sys.version_info >= (3, 10),
        f"{platform.python_version()} on {platform.system()} {platform.machine()}",
        "3.10+ required" if sys.version_info < (3, 10) else ""))

    version = sqlite3.sqlite_version
    checks.append(_check("sqlite", True, f"{version}", ""))
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        checks.append(_check("sqlite fts5", True, "available", ""))
    except sqlite3.OperationalError as exc:
        checks.append(_check("sqlite fts5", False, str(exc),
                             "retrieval falls back to vector-only search"))

    try:
        import numpy
        checks.append(_check("numpy", True, numpy.__version__, ""))
    except ImportError:
        checks.append(_check("numpy", False, "not installed",
                             "pip install numpy — the store and vectors need it"))

    try:
        import torch
        cuda = torch.cuda.is_available()
        detail = f"{torch.__version__}" + (" (cuda)" if cuda else " (cpu)")
        checks.append(_check("torch", True, detail,
                             "" if cuda else "CPU training is a demonstration, not a build"))
    except ImportError:
        checks.append(_check("torch", False, "not installed",
                             "harvest/store/query/tokenize work; train and generate do not"))

    try:
        import tokenizers
        checks.append(_check("tokenizers", True, tokenizers.__version__, ""))
    except ImportError:
        checks.append(_check("tokenizers", False, "not installed",
                             "the pure-python BPE is used: correct, slow above ~1M chars"))

    try:
        rules = load_rules()
        problems = rules.check()
        checks.append(_check(
            "rules", not problems,
            f"{len(rules)} rules across {len(rules.level_counts())} levels"
            + (f"; {len(problems)} problem(s)" if problems else ""),
            "; ".join(problems[:3])))
    except Exception as exc:
        checks.append(_check("rules", False, f"{type(exc).__name__}: {exc}",
                             "the rule set is the spine of the pipeline"))

    try:
        registry = load_registry()
        checks.append(_check(
            "languages", True,
            f"{len(registry)} profiles: {len(registry.natural())} natural, "
            f"{len(registry.code_languages())} programming", ""))
    except Exception as exc:
        checks.append(_check("languages", False, f"{type(exc).__name__}: {exc}", ""))

    root = resolve(cfg, "root")
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, ".write-test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        usage = shutil.disk_usage(root)
        free_gb = usage.free / (1024 ** 3)
        checks.append(_check("data dir", True,
                             f"{root} writable, {free_gb:.1f} GB free",
                             "" if free_gb > 2 else "under 2 GB free"))
    except OSError as exc:
        checks.append(_check("data dir", False, f"{root}: {exc}",
                             "choose another --root"))

    keys = {name: bool(os.environ.get(name)) for name in
            ("TAVILY_API_KEY", "BRAVE_API_KEY", "SEARXNG_URL")}
    configured = [name for name, present in keys.items() if present]
    checks.append(_check(
        "search backends", True,
        "configured: " + (", ".join(configured) if configured else "none (fixtures and ddg still work)"),
        ""))

    db_path = resolve(cfg, "db")
    if os.path.exists(db_path):
        size_mb = os.path.getsize(db_path) / (1024 ** 2)
        checks.append(_check("store", True, f"{db_path} ({size_mb:.1f} MB)", ""))
    else:
        checks.append(_check("store", True, "not created yet",
                             "run `vrusha harvest` or `vrusha ingest`"))

    tokenizer_path = os.path.join(resolve(cfg, "tokens"), "tokenizer.json")
    checks.append(_check(
        "tokenizer", True,
        tokenizer_path if os.path.exists(tokenizer_path) else "not trained yet",
        "" if os.path.exists(tokenizer_path) else "run `vrusha tokenize`"))

    return checks


def format_checks(checks: List[dict]) -> str:
    lines = [f"vrusha {__version__} — doctor", ""]
    for check in checks:
        mark = "ok  " if check["ok"] else "FAIL"
        lines.append(f"  [{mark}] {check['name']:<16} {check['detail']}")
        if check["consequence"]:
            lines.append(f"         {'':<16} -> {check['consequence']}")
    failed = [c for c in checks if not c["ok"]]
    lines.append("")
    lines.append(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return "\n".join(lines)
