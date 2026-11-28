"""Snapshots: a reader pins the index it is using, a writer may replace it.

Compaction swaps the index files in place. That is safe for a reader that has
already loaded an `AnnIndex` — it holds arrays, not file handles — but it is safe
by accident, not by design, and it says nothing about the reader that is *about*
to load while a swap is happening.

This makes it explicit, with three small things:

- a **generation** number in a `CURRENT` file, bumped after every swap;
- a **pin**, a file naming a generation that a reader is using;
- **gc**, which deletes old generation directories except the ones that are
  pinned and the newest few.

The rule that matters: **a pinned generation is never deleted.** A long query, a
batch job or a slow reader can hold a snapshot for as long as it likes without a
compaction pulling the files out from under it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

CURRENT = "CURRENT"
PINS = "pins"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_current(index_dir: str) -> dict:
    path = os.path.join(index_dir, CURRENT)
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return {"generation": int(payload.get("generation", 0)),
                "updated": str(payload.get("updated", ""))}
    except (OSError, ValueError, TypeError):
        return {"generation": 0, "updated": ""}


def publish(index_dir: str) -> dict:
    """Bump the generation after a swap. Atomic: written then renamed."""
    os.makedirs(index_dir, exist_ok=True)
    current = read_current(index_dir)
    payload = {"generation": current["generation"] + 1, "updated": _now()}
    staging = os.path.join(index_dir, CURRENT + ".tmp")
    with open(staging, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(staging, os.path.join(index_dir, CURRENT))
    return payload


@dataclass
class Snapshot:
    """A pinned view of the index. `release()` when done — always."""

    index_dir: str
    generation: int
    token: str

    @property
    def pinned(self) -> bool:
        return os.path.exists(os.path.join(self.index_dir, PINS, self.token))

    def release(self) -> bool:
        path = os.path.join(self.index_dir, PINS, self.token)
        try:
            os.unlink(path)
            return True
        except OSError:
            return False

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def pin(index_dir: str, *, name: str = "") -> Snapshot:
    """Pin the current generation. The caller must release it."""
    current = read_current(index_dir)
    token = f"{int(time.time())}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    directory = os.path.join(index_dir, PINS)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, token), "w", encoding="utf-8") as handle:
        json.dump({"generation": current["generation"], "name": name,
                   "created": _now(), "pid": os.getpid()}, handle)
    return Snapshot(index_dir=index_dir, generation=current["generation"],
                    token=token)


def pins(index_dir: str) -> List[dict]:
    directory = os.path.join(index_dir, PINS)
    if not os.path.isdir(directory):
        return []
    found = []
    for name in sorted(os.listdir(directory)):
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["token"] = name
            found.append(payload)
        except (OSError, ValueError):
            continue
    return found


def pinned_generations(index_dir: str) -> set:
    return {int(item.get("generation", 0)) for item in pins(index_dir)}


def gc(index_dir: str, *, keep: int = 2) -> dict:
    """Delete old generation directories — never a pinned one.

    `keep` counts generations *below* the current one to retain, so a reader that
    pinned just before a swap still finds its files if the pin is lost.
    """
    current = read_current(index_dir)["generation"]
    pinned = pinned_generations(index_dir)
    removed, kept = [], []
    if not os.path.isdir(index_dir):
        return {"removed": removed, "kept": kept, "current": current}
    for name in sorted(os.listdir(index_dir)):
        if not name.startswith("gen-"):
            continue
        try:
            generation = int(name.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        if generation == current or generation in pinned:
            kept.append(name)
            continue
        if generation > current - keep:
            kept.append(name)
            continue
        path = os.path.join(index_dir, name)
        for entry in os.listdir(path):
            try:
                os.unlink(os.path.join(path, entry))
            except OSError:
                pass
        try:
            os.rmdir(path)
            removed.append(name)
        except OSError:
            kept.append(name)
    return {"removed": removed, "kept": kept, "current": current}


def load(index_dir: str, *, pin_it: bool = True):
    """Load the current generation's index, optionally pinned.

    Returns `(snapshot_or_None, index_or_None)`. The snapshot is `None` when the
    caller asked not to pin, which is right for a one-shot CLI query.
    """
    from . import ann as ann_mod

    generation = read_current(index_dir)["generation"]
    directory = index_dir
    if generation:
        candidate = os.path.join(index_dir, f"gen-{generation}")
        if os.path.isdir(candidate):
            directory = candidate
    index = ann_mod.AnnIndex.load(directory)
    if index is None:
        return None, None
    return (pin(index_dir) if pin_it else None), index
