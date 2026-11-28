"""Reranking: a second look at the top of the list, behind a seam.

Fusion gets the right chunk into the top twenty. It does not put it at rank one —
measured on this repository's own documentation, `recall@5` is 70% while
`recall@1` is 35%. Half of the questions that *could* be answered put the right
document somewhere in the top five and the wrong one first.

This module is the **seam**, not the reranker. Part 14 of `docs/PLAN-V3.md`:

- a protocol every reranker satisfies,
- a no-op that changes nothing, as the default,
- a registry so a backend is named in config and not hard-coded,
- and a guarantee that a broken reranker degrades instead of failing: a second
  stage that can take a query down is worse than no second stage.

Part 15 adds a lexical reranker that needs no model. Part 16 adds an optional
cross-encoder. Part 17 measures all three and decides whether any of them earned
its place — and "none of them" is a valid answer.

Why a seam first: without one, "add reranking" means editing the retriever, and
then there is no way to run the same 40 questions with and without it on the same
day. The seam is what makes the comparison possible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence

from .store.query import Hit


class Reranker(Protocol):
    """What a reranker must offer. Deliberately small."""

    name: str

    def available(self) -> bool:
        ...

    def rerank(self, query: str, hits: Sequence[Hit], k: int) -> List[Hit]:
        ...


@dataclass
class NoOpReranker:
    """Changes nothing, and says so.

    The default, because a reranker that has not been measured against the
    absence of a reranker has not been shown to do anything.
    """

    name: str = "none"

    def available(self) -> bool:
        return True

    def rerank(self, query: str, hits: Sequence[Hit], k: int) -> List[Hit]:
        return list(hits)[:k]


class RerankError(RuntimeError):
    """A reranker that cannot run. Never fatal: the retriever falls back."""


#: name -> factory. A reranker that needs a model registers itself here and is
#: only built when the config names it, so `import vrusha` stays dependency-free.
RERANKERS: Dict[str, callable] = {"none": NoOpReranker}


def register(name: str, factory) -> None:
    RERANKERS[name] = factory


def build_reranker(cfg: Optional[dict] = None) -> Reranker:
    """The configured reranker, or the no-op with the reason in its name."""
    block = ((cfg or {}).get("rerank", {}) or {})
    name = str(block.get("backend") or "none").strip().lower()
    factory = RERANKERS.get(name)
    if factory is None:
        raise RerankError(f"unknown reranker {name!r}; available: "
                          f"{', '.join(sorted(RERANKERS))}")
    reranker = factory()
    if not reranker.available():
        # not an error: a machine without the optional extra still answers
        # questions, just without the second stage
        return NoOpReranker()
    return reranker


def apply(reranker: Optional[Reranker], query: str, hits: Sequence[Hit],
          k: int, notes: Optional[list] = None) -> List[Hit]:
    """Run the second stage, and never let it take the query down.

    A reranker that raises is a bug in the reranker, not a reason to fail a
    search: the first stage already produced a defensible order, so the fallback
    is that order plus a note saying the second stage failed.
    """
    if reranker is None or not hits:
        return list(hits)[:k]
    try:
        reranked = reranker.rerank(query, hits, k)
    except Exception as exc:                 # noqa: BLE001 - see the docstring
        if notes is not None:
            notes.append(f"rerank {reranker.name} failed "
                         f"({type(exc).__name__}); using the fused order")
        return list(hits)[:k]
    if not isinstance(reranked, list):
        reranked = list(reranked)
    # the reranker may reorder and drop, but it may not invent: every returned
    # hit must be one it was given
    allowed = {hit.chunk_id for hit in hits}
    kept = [hit for hit in reranked if hit.chunk_id in allowed]
    if len(kept) != len(reranked) and notes is not None:
        notes.append(f"rerank {reranker.name} returned "
                     f"{len(reranked) - len(kept)} hit(s) it was not given; dropped")
    return kept[:k]
