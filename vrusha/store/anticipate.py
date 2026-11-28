"""Anticipation: warm the next query before it is asked, within a budget.

An agent that waits for its next task and *then* retrieves pays the full query cost
on the first turn. Anticipation pays it in the gap before the task arrives, which is
free time on an idle worker.

It is a bet, and the design is about what happens when the bet is wrong:

- **a hard cap on how many tasks are prefetched** (3 by default) — a wrong guess
  wastes bounded work, never a run;
- **a time budget** — prefetch stops when it is spent, so a slow query cannot delay
  the real one;
- **the cache is small and expires** — a stale prefetch is worse than none, because
  it looks like a warm hit.

The prediction is deliberately simple: open `question` entries on the board, then
unclaimed tasks from the claims table. Anything cleverer would be a model, and a
model that predicts tasks has to be measured before it is trusted with the budget.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

#: How many tasks may be prefetched. A wrong guess wastes bounded work.
MAX_TASKS = 3

#: How long prefetching may take, in milliseconds.
BUDGET_MS = 500.0

#: How long a prefetched entry stays valid. A stale hit looks like a warm one.
TTL_S = 60.0


@dataclass
class Cache:
    """Small, bounded, expiring. Nothing here is allowed to grow without a cap."""

    entries: Dict[str, tuple] = field(default_factory=dict)
    limit: int = 32
    ttl_s: float = TTL_S
    hits: int = 0
    misses: int = 0

    def put(self, key: str, value, *, now: Optional[float] = None) -> None:
        stamp = time.time() if now is None else now
        if len(self.entries) >= self.limit:
            oldest = min(self.entries, key=lambda k: self.entries[k][1])
            self.entries.pop(oldest, None)
        self.entries[key] = (value, stamp)

    def get(self, key: str, *, now: Optional[float] = None):
        stamp = time.time() if now is None else now
        found = self.entries.get(key)
        if found is None:
            self.misses += 1
            return None
        value, created = found
        if stamp - created > self.ttl_s:
            self.entries.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return value

    def __len__(self) -> int:
        return len(self.entries)


def predict(board, claims=None, *, limit: int = MAX_TASKS) -> List[str]:
    """What is likely to be asked next: open questions, then unclaimed tasks."""
    guesses: List[str] = []
    try:
        for entry in board.since_of_kind("question", limit=limit * 2):
            text = (getattr(entry, "summary", "") or "").strip()
            if text and text not in guesses:
                guesses.append(text)
    except Exception:                        # noqa: BLE001 - prediction is optional
        pass
    if claims is not None and len(guesses) < limit:
        try:
            for row in claims.summary_rows():
                if row.get("state") == "claimed":
                    continue
                task = str(row.get("task", "")).strip()
                if task and task not in guesses:
                    guesses.append(task)
        except Exception:                    # noqa: BLE001
            pass
    return guesses[:limit]


def warm(cache: Cache, store, embedder, cfg: dict, tasks: Sequence[str], *,
         budget_ms: float = BUDGET_MS, top_k: int = 5) -> dict:
    """Retrieve for each predicted task, while the budget lasts."""
    from .query import retrieve

    started = time.perf_counter()
    warmed, skipped = 0, 0
    for task in tasks:
        if (time.perf_counter() - started) * 1000.0 > budget_ms:
            skipped += 1
            continue
        if cache.get(task) is not None:
            continue
        try:
            result = retrieve(store, embedder, task, cfg, top_k=top_k)
        except Exception:                    # noqa: BLE001 - a bad guess is not fatal
            skipped += 1
            continue
        cache.put(task, result)
        warmed += 1
    return {"warmed": warmed, "skipped": skipped,
            "ms": round((time.perf_counter() - started) * 1000.0, 1),
            "cached": len(cache)}


def lookup(cache: Cache, store, embedder, cfg: dict, query: str, *, top_k: int = 5):
    """A warm hit if there is one, otherwise retrieve and cache it."""
    from .query import retrieve

    hit = cache.get(query)
    if hit is not None:
        return hit, True
    result = retrieve(store, embedder, query, cfg, top_k=top_k)
    cache.put(query, result)
    return result, False
