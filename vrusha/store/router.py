"""Which engine answers this query, and why.

Two backends that are each best under different conditions need something that
knows the conditions. That is this file. It is not a load balancer and it is not
an LLM: it is a policy with a short memory, and every decision it makes comes
back with a sentence a human can check.

The order is deliberate, cheapest and most decisive gate first:

    1  need        does the query require a capability?  filter -> RagForge
    2  resource    is there RAM for the in-process index?  no -> RagForge
    3  freshness   is the native index stale?            yes -> RagForge
    4  speed       which one has been faster lately?      EWMA, with hysteresis
    5  failure     did one just fail?                     demote, cool down

A gate that fires stops the walk. A query that needs a filter never reaches
step 4, because a faster backend that cannot filter is not a candidate.

Every decision is measured: the router records how long each backend actually
took and uses that for the next query, so the choice follows the machine rather
than a table someone wrote on a different machine.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .backends import (BM25, FILTER, SORT, Backend, free_ram_mb,
                       build_backends)

Hit = Tuple[int, float]


@dataclass
class Decision:
    """The choice, the reason, and what was passed over."""

    backend: str
    reason: str
    gate: str
    needs: Tuple[str, ...] = ()
    skipped: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"backend": self.backend, "reason": self.reason, "gate": self.gate,
                "needs": list(self.needs), "skipped": self.skipped}

    def __str__(self) -> str:
        return f"{self.backend} ({self.gate}: {self.reason})"


class Router:
    """Pick a backend per query, remember how it went, fail over quietly."""

    def __init__(self, store, embedder, cfg: dict,
                 backends: Optional[Dict[str, Backend]] = None):
        self.store = store
        self.embedder = embedder
        self.cfg = cfg
        self.backends = backends if backends is not None else \
            build_backends(store, cfg, embedder)
        block = cfg.get("backends", {}) or {}
        self.mode = str(block.get("mode", "auto")).lower()
        self.cooldown_s = float(block.get("cooldown_s", 30.0))
        self.hysteresis = float(block.get("hysteresis", 0.20))
        self.min_free_mb = float(block.get("min_free_mb", 256.0))
        self.shadow_every = int(block.get("shadow_every", 0))
        self.min_vectors = int((cfg.get("ann", {}) or {}).get("min_vectors", 20_000))
        self._lock = threading.Lock()
        self._latency: Dict[str, float] = {}     # EWMA, milliseconds
        self._samples: Dict[str, int] = {}
        self._errors: Dict[str, int] = {}
        self._demoted: Dict[str, float] = {}     # backend -> unix time until
        self._queries = 0

    # -- memory ------------------------------------------------------------

    def _record(self, name: str, milliseconds: float) -> None:
        with self._lock:
            previous = self._latency.get(name)
            # EWMA, weighted toward history so one slow query cannot flip the
            # choice, but fast enough to follow a machine that changes state.
            self._latency[name] = milliseconds if previous is None else \
                (previous * 0.7 + milliseconds * 0.3)
            self._samples[name] = self._samples.get(name, 0) + 1

    def _fail(self, name: str) -> None:
        with self._lock:
            self._errors[name] = self._errors.get(name, 0) + 1
            self._demoted[name] = time.time() + self.cooldown_s

    def _cooling(self, name: str) -> bool:
        until = self._demoted.get(name, 0.0)
        return time.time() < until

    def latency(self, name: str) -> Optional[float]:
        return self._latency.get(name)

    # -- the decision ------------------------------------------------------

    def plan(self, *, need: Sequence[str] = (), filter: Optional[dict] = None,
             rows: int = 0) -> Tuple[List[str], Decision]:
        """Ordered candidate names, plus the decision that produced the order.

        The first name is the intended backend. The rest are the fallbacks, in
        the order they will be tried if the first one raises.
        """
        needs = list(need)
        if filter:
            needs.append(FILTER)
        skipped: List[str] = []

        def usable(name: str, *required: str) -> bool:
            backend = self.backends.get(name)
            if backend is None:
                skipped.append(f"{name}: not configured")
                return False
            if required and not backend.can(*required):
                missing = [r for r in required if r not in backend.capabilities]
                skipped.append(f"{name}: cannot {', '.join(missing)}")
                return False
            if self._cooling(name):
                skipped.append(f"{name}: cooling down after a failure")
                return False
            return True

        def available(name: str) -> bool:
            try:
                ok = self.backends[name].available()
            except Exception:
                ok = False
            if not ok:
                skipped.append(f"{name}: not available")
            return ok

        # 1 — capability. A filter is a requirement, not a preference.
        if self.mode == "exact":
            return ["exact"], Decision("exact", "backends.mode is exact",
                                       "mode", tuple(needs), skipped)
        if FILTER in needs or SORT in needs:
            if usable("ragforge", FILTER) and available("ragforge"):
                return (["ragforge"],
                        Decision("ragforge", "the query needs a filter, and only "
                                 "RagForge can filter before ranking",
                                 "need", tuple(needs), skipped))
            return (["exact"],
                    Decision("exact", "a filter was requested but no backend that "
                             "supports filters is reachable; filtering after "
                             "retrieval instead", "need", tuple(needs), skipped))

        native_ok = usable("native") and available("native")
        rag_ok = usable("ragforge") and available("ragforge")
        exact_ok = usable("exact") and available("exact")

        # 2 — resource. The in-process index is RAM; RagForge's is another
        # process's RAM, which can be capped and restarted independently.
        if native_ok and rag_ok:
            free = free_ram_mb()
            rows = rows or self._rows()
            need_mb = self._index_mb()
            if free is not None and need_mb and free < max(self.min_free_mb, need_mb):
                return (["ragforge", "native"],
                        Decision("ragforge",
                                 f"only {free:.0f} MB free, the native index wants "
                                 f"~{need_mb:.0f} MB; RagForge keeps that memory "
                                 f"in its own process",
                                 "resource", tuple(needs), skipped))

        # 3 — freshness. A stale native index answers from a corpus that no
        # longer exists, so it is not a candidate at all.
        if rag_ok and not native_ok and "native: not available" in " ".join(skipped):
            return (["ragforge", "exact"],
                    Decision("ragforge", "the native index is missing or stale; "
                             "RagForge is serving the vector signal",
                             "freshness", tuple(needs), skipped))

        # 4 — speed. Prefer the one that has been faster, but only switch on a
        # clear margin: an index that is 5% ahead is not worth a different code
        # path, and flapping between two engines hides real regressions.
        candidates = [name for name, ok in
                      (("native", native_ok), ("ragforge", rag_ok)) if ok]
        if len(candidates) == 2:
            # Exploration before exploitation. A backend that has never been
            # timed can never win on time, so it would never be tried — and the
            # router would stay on whichever one it happened to see first. Try
            # the unmeasured one once, then let the measurements decide.
            unmeasured = [name for name in candidates if name not in self._latency]
            if len(unmeasured) == 1:
                measured = [name for name in candidates if name != unmeasured[0]][0]
                return ([unmeasured[0], measured] + (["exact"] if exact_ok else []),
                        Decision(unmeasured[0],
                                 f"first probe of {unmeasured[0]}: it has never "
                                 f"been timed, and an untried backend cannot win",
                                 "explore", tuple(needs), skipped))
            fast, slow = min(candidates, key=lambda n: self._latency.get(n, float("inf"))), \
                max(candidates, key=lambda n: self._latency.get(n, float("inf")))
            first, second = self._latency.get(fast), self._latency.get(slow)
            if first is None:
                fast, slow = "native", "ragforge"     # nothing measured yet
            elif second is not None and first > second * (1.0 - self.hysteresis):
                # too close to call: keep the current default rather than flap
                fast, slow = "native", "ragforge"
            return ([fast, slow] + (["exact"] if exact_ok else []),
                    Decision(fast,
                             f"{fast} has been faster lately"
                             + (f" ({self._latency.get(fast, 0):.2f} ms vs "
                                f"{self._latency.get(slow, 0):.2f} ms EWMA)"
                                if self._latency.get(fast) is not None else ""),
                             "speed", tuple(needs), skipped))

        if candidates:
            name = candidates[0]
            return ([name] + (["exact"] if exact_ok and name != "exact" else []),
                    Decision(name, f"the only usable backend ({name})",
                             "only", tuple(needs), skipped))

        if exact_ok:
            return (["exact"], Decision("exact", "no index is usable; the scan "
                                        "always answers", "fallback",
                                        tuple(needs), skipped))
        return ([], Decision("none", "no backend can answer this query",
                             "none", tuple(needs), skipped))

    # -- the query ---------------------------------------------------------

    def search(self, vector: np.ndarray, k: int, *, text: str = "",
               filter: Optional[dict] = None, need: Sequence[str] = (),
               nprobe: int = 0) -> Tuple[List[Hit], Decision, Dict[str, float]]:
        """Ask the best backend, fall through the others on failure.

        Returns hits, the decision that was *actually* served, and the timing of
        each attempt. A backend that raises is demoted for `cooldown_s` and the
        next candidate answers — a query gets slower, never an error.
        """
        order, decision = self.plan(need=need, filter=filter)
        timings: Dict[str, float] = {}
        if not order:
            # Nothing can answer. That is an empty result, not an exception: a
            # store with no vectors is a legitimate state, and the caller
            # already handles "the vector signal found nothing".
            return [], decision, timings
        last_error: Optional[BaseException] = None

        for name in order:
            backend = self.backends.get(name)
            if backend is None:
                continue
            started = time.perf_counter()
            try:
                hits = backend.search(vector, k, text=text, filter=filter,
                                      nprobe=nprobe)
            except Exception as exc:      # noqa: BLE001 - any backend may fail
                elapsed = (time.perf_counter() - started) * 1000
                timings[name] = round(elapsed, 2)
                self._fail(name)
                last_error = exc
                continue
            elapsed = (time.perf_counter() - started) * 1000
            timings[name] = round(elapsed, 2)
            self._record(name, elapsed)
            if name != decision.backend:
                # the plan was wrong or the first choice broke: say so, and
                # remember the reason for the next plan.
                decision = Decision(name, f"{decision.backend} did not answer; "
                                    f"{name} served the query",
                                    "failover", decision.needs, decision.skipped)
            return hits, decision, timings

        raise RuntimeError(
            f"no backend answered: {last_error}" if last_error
            else "no backend can answer this query")

    # -- reporting ---------------------------------------------------------

    def _rows(self) -> int:
        try:
            return int(self.store.vector_fingerprint()[0])
        except Exception:
            return 0

    def _index_mb(self) -> float:
        backend = self.backends.get("native")
        if backend is None:
            return 0.0
        try:
            stats = backend.stats()
        except Exception:
            return 0.0
        return stats.bytes / 1e6

    def _status(self, name: str, stats: dict) -> str:
        """One word for why a backend is or is not in the rotation."""
        if self._cooling(name):
            return "cooling"
        if stats.get("available"):
            return "ready"
        detail = stats.get("detail") or stats
        if detail.get("index") == "on disk" and detail.get("stale"):
            return "stale"
        if detail.get("index") == "on disk":
            return "small"          # built, but under ann.min_vectors
        if name == "ragforge" and not (self.cfg.get("backends", {}) or {}).get(
                "ragforge", {}).get("url"):
            return "not configured"
        return "down"

    def report(self) -> List[dict]:
        rows = self._rows()
        output = []
        for name, backend in self.backends.items():
            try:
                stats = backend.stats().to_dict()
            except Exception as exc:      # noqa: BLE001
                stats = {"name": name, "available": False, "error": str(exc)}
            stats["status"] = self._status(name, stats)
            stats["ewma_ms"] = (round(self._latency[name], 2)
                                if name in self._latency else None)
            stats["samples"] = self._samples.get(name, 0)
            stats["errors"] = self._errors.get(name, 0)
            stats["cooling"] = self._cooling(name)
            stats["capabilities"] = sorted(backend.capabilities)
            output.append(stats)
        free = free_ram_mb()
        if output:
            output[0]["free_ram_mb"] = round(free, 0) if free else None
            output[0]["vectors_in_store"] = rows
        return output
