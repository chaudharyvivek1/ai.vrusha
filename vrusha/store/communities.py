"""Communities over the entity graph, by label propagation.

`edges.py` produces relations; a relation list is still not a knowledge graph. What
makes it one is knowing which entities belong together — the cluster around
`Acme Corporation`, the cluster around `Reserve Bank of India` — because a query
that lands on one member should be able to see the rest.

**Label propagation**, not Louvain: it is twenty lines, needs no dependency, and is
deterministic when the node order is fixed and updates are applied in that order.
Louvain finds slightly better partitions and brings modularity arithmetic and a
tie-breaking rule that has to be argued about. For a graph of this size the
difference is not worth the surface area.

The rule that makes it reproducible: **nodes are visited in sorted order, and a
tie between two candidate labels goes to the lexicographically smaller one.** A
community detection that gives a different answer on a second run cannot be
tested, and cannot be cited.
"""
from __future__ import annotations

import json
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_communities (
  name       TEXT PRIMARY KEY,
  community  INTEGER NOT NULL,
  size       INTEGER NOT NULL,
  degree     INTEGER NOT NULL,
  detected   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community ON x_communities(community);
"""

#: Label propagation converges quickly on small graphs; past this it is oscillating.
MAX_ROUNDS = 20


def adjacency(pairs: Iterable[Tuple[str, str]]) -> Dict[str, set]:
    """Undirected graph from (subject, object) pairs. Case-folded for matching."""
    graph: Dict[str, set] = {}
    for left, right in pairs:
        a = (left or "").strip().lower()
        b = (right or "").strip().lower()
        if not a or not b or a == b:
            continue
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    return graph


def propagate(graph: Dict[str, set], *, rounds: int = MAX_ROUNDS) -> Dict[str, int]:
    """Label propagation. Every node starts in its own community."""
    labels = {node: index for index, node in enumerate(sorted(graph))}
    for _ in range(max(1, rounds)):
        changed = False
        for node in sorted(graph):
            neighbours = graph.get(node) or set()
            if not neighbours:
                continue
            counts: Dict[int, int] = {}
            for neighbour in neighbours:
                label = labels.get(neighbour, labels[node])
                counts[label] = counts.get(label, 0) + 1
            best = max(counts.values())
            # ties go to the smaller label, so the result does not depend on
            # dictionary order -- a community detector that changes answer on a
            # second run cannot be tested
            winner = min(label for label, count in counts.items() if count == best)
            if winner != labels[node]:
                labels[node] = winner
                changed = True
        if not changed:
            break
    # renumber densely, in sorted order, so ids are stable across runs
    order = {old: new for new, old in
             enumerate(sorted({labels[node] for node in sorted(labels)}))}
    return {node: order[labels[node]] for node in labels}


def summarise(labels: Dict[str, int],
              graph: Optional[Dict[str, set]] = None) -> Dict[str, object]:
    sizes: Dict[int, int] = {}
    for community in labels.values():
        sizes[community] = sizes.get(community, 0) + 1
    return {"nodes": len(labels), "communities": len(sizes),
            "largest": max(sizes.values()) if sizes else 0,
            "singletons": sum(1 for size in sizes.values() if size == 1)}


def detect(store, *, rounds: int = MAX_ROUNDS) -> Dict[str, int]:
    """Communities from the edges already stored."""
    from .edges import ensure_schema

    ensure_schema(store)
    rows = store.conn.execute(
        "SELECT subject, object FROM x_edges").fetchall()
    graph = adjacency((row["subject"], row["object"]) for row in rows)
    return propagate(graph, rounds=rounds)


def store_communities(store, labels: Dict[str, int],
                      graph: Optional[Dict[str, set]] = None) -> int:
    """Persist the labels, replacing what was there."""
    store.conn.executescript(SCHEMA)
    if graph is None:
        graph = adjacency([])
    sizes: Dict[int, int] = {}
    for community in labels.values():
        sizes[community] = sizes.get(community, 0) + 1
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.conn.execute("DELETE FROM x_communities")
    written = 0
    for name, community in labels.items():
        store.conn.execute(
            "INSERT OR REPLACE INTO x_communities (name, community, size, "
            "degree, detected) VALUES (?,?,?,?,?)",
            (name, int(community), int(sizes.get(community, 1)),
             len(graph.get(name) or ()), stamp))
        written += 1
    store.commit()
    return written


def build(store, *, rounds: int = MAX_ROUNDS) -> dict:
    """Detect, store, and report — what `vrusha communities --build` calls."""
    labels = detect(store, rounds=rounds)
    rows = store.conn.execute("SELECT subject, object FROM x_edges").fetchall()
    graph = adjacency((row["subject"], row["object"]) for row in rows)
    store_communities(store, labels, graph)
    return {**summarise(labels, graph), "stored": len(labels)}


def community_of(store, name: str) -> Optional[int]:
    store.conn.executescript(SCHEMA)
    row = store.conn.execute(
        "SELECT community FROM x_communities WHERE name = ?",
        ((name or "").strip().lower(),)).fetchone()
    return int(row["community"]) if row else None


def members(store, community: int, *, limit: int = 50) -> List[dict]:
    store.conn.executescript(SCHEMA)
    rows = store.conn.execute(
        "SELECT name, size, degree FROM x_communities WHERE community = ? "
        "ORDER BY degree DESC, name LIMIT ?", (int(community), limit)).fetchall()
    return [dict(row) for row in rows]


def list_communities(store, *, limit: int = 20) -> List[dict]:
    store.conn.executescript(SCHEMA)
    rows = store.conn.execute(
        "SELECT community, size, COUNT(*) AS named FROM x_communities "
        "GROUP BY community ORDER BY size DESC, community LIMIT ?",
        (limit,)).fetchall()
    return [dict(row) for row in rows]
