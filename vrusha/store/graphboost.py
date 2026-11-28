"""Bounded graph ranking: let the graph help, and never let it decide.

A chunk whose entities sit in the same community as the query's entities is
slightly more likely to be the answer. "Slightly" is the whole design: the boost is
**multiplicative and capped**, so the graph can reorder near-ties and cannot
overturn a clear lexical win.

The cap is not caution, it is arithmetic. A graph built from prose extraction is
incomplete — `docs/work/part-22-prose-entities.md` lists what the rules miss — so a
graph signal is evidence of a *possible* connection, not proof of a relevant one. An
unbounded boost would let a missing entity break a chain and a spurious one create
a false order.

Measured on the same 40 questions as everything else, and the result is reported
whether it helps or not.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

#: How much the graph may move a score. 0.15 = at most 15%, which reorders hits
#: within about one position of each other and nothing more.
DEFAULT_WEIGHT = 0.15

#: How many hits may be boosted. A boost applied to everything is a constant, and
#: a constant reorders nothing — it only costs time.
DEFAULT_MAX_HITS = 5


def _communities_by_chunk(store) -> Dict[int, set]:
    """chunk_id -> the community ids of the entities its edges mention."""
    try:
        rows = store.conn.execute(
            "SELECT e.chunk_id, c.community FROM x_edges e "
            "JOIN x_communities c ON c.name = lower(e.subject) "
            "WHERE e.chunk_id > 0").fetchall()
        rows += store.conn.execute(
            "SELECT e.chunk_id, c.community FROM x_edges e "
            "JOIN x_communities c ON c.name = lower(e.object) "
            "WHERE e.chunk_id > 0").fetchall()
    except Exception:                        # noqa: BLE001 - tables may not exist
        return {}
    mapping: Dict[int, set] = {}
    for row in rows:
        mapping.setdefault(int(row["chunk_id"]), set()).add(int(row["community"]))
    return mapping


def _query_communities(store, query: str) -> set:
    """Which communities the query's words belong to."""
    from .prose import extract as extract_entities

    names = {entity.text.lower() for entity in extract_entities(query)}
    if not names:
        return set()
    try:
        rows = store.conn.execute(
            "SELECT community FROM x_communities WHERE name IN "
            f"({','.join('?' * len(names))})", list(names)).fetchall()
    except Exception:                        # noqa: BLE001
        return set()
    return {int(row["community"]) for row in rows}


def boost(store, query: str, hits: Sequence, *, weight: float = DEFAULT_WEIGHT,
          max_hits: int = DEFAULT_MAX_HITS,
          notes: Optional[list] = None) -> List:
    """Reorder hits by a bounded graph signal. Returns the same hit objects.

    A hit is boosted when a community its chunk belongs to is also a community the
    query mentions. The boost is `1 + weight`, applied to the highest-scoring
    `max_hits` such hits only.
    """
    hits = list(hits)
    if not hits or weight <= 0:
        return hits
    communities = _query_communities(store, query)
    if not communities:
        return hits
    by_chunk = _communities_by_chunk(store)
    if not by_chunk:
        return hits

    scored: List[Tuple[float, int, object]] = []
    boosted = 0
    for position, hit in enumerate(hits):
        base = float(getattr(hit, "scores", {}).get("fused", 0.0))
        shared = by_chunk.get(int(getattr(hit, "chunk_id", 0)), set()) & communities
        if shared and boosted < max_hits:
            base *= (1.0 + float(weight))
            boosted += 1
            if hasattr(hit, "scores"):
                hit.scores["graph"] = round(float(weight), 3)
        scored.append((base, position, hit))

    if not boosted:
        return hits
    if notes is not None:
        notes.append(f"graph: boosted {boosted} hit(s) by up to {weight:.0%}")
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [hit for _score, _position, hit in scored]
