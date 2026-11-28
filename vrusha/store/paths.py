"""Multi-hop paths: which two entities connect this one to that one.

Flat retrieval answers "which chunk mentions X". A graph answers "how is X
connected to Y", and that is a question a chunk cannot answer at all — the answer
is a *path*, and each hop on it is an edge with its own evidence.

Breadth-first, capped at `max_hops`, returning the shortest path. Two properties
matter more than speed:

**Every hop carries its evidence.** A path is only as good as its weakest edge, so
each step returns the sentence that asserted it; a caller can print the chain and
a reader can check every link.

**The hop cap is a hard limit, not a preference.** Two hops on a graph of business
relations is a plausible claim; five hops is "everything is connected to everything
in six steps", which is true and useless. The cap is what keeps a path an answer
rather than a coincidence.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple


def _graph(store) -> Dict[str, List[tuple]]:
    """Undirected adjacency with the evidence attached to each hop."""
    from .edges import ensure_schema

    ensure_schema(store)
    rows = store.conn.execute(
        "SELECT subject, predicate, object, sentence, confidence FROM x_edges"
    ).fetchall()
    graph: Dict[str, List[tuple]] = {}
    for row in rows:
        left = str(row["subject"]).strip().lower()
        right = str(row["object"]).strip().lower()
        if not left or not right or left == right:
            continue
        hop = {"predicate": row["predicate"], "sentence": row["sentence"],
               "confidence": float(row["confidence"])}
        graph.setdefault(left, []).append({**hop, "from": left, "to": right})
        graph.setdefault(right, []).append({**hop, "from": right, "to": left})
    return graph


def find(store, start: str, goal: str, *, max_hops: int = 2) -> Optional[dict]:
    """The shortest path between two names, each hop with its evidence."""
    a = (start or "").strip().lower()
    b = (goal or "").strip().lower()
    if not a or not b:
        return None
    if a == b:
        return {"start": a, "goal": b, "hops": 0, "path": [],
                "confidence": 1.0}
    graph = _graph(store)
    if a not in graph or b not in graph:
        return None

    # breadth-first, so the first time we reach the goal is the shortest path
    queue: List[Tuple[str, List[dict]]] = [(a, [])]
    seen = {a}
    while queue:
        node, trail = queue.pop(0)
        if len(trail) >= max_hops:
            continue
        for hop in sorted(graph.get(node, []),
                          key=lambda item: (-item["confidence"], item["to"])):
            nxt = hop["to"]
            if nxt in seen:
                continue
            path = trail + [hop]
            if nxt == b:
                confidence = min(step["confidence"] for step in path)
                return {"start": a, "goal": b, "hops": len(path),
                        "path": path, "confidence": confidence}
            seen.add(nxt)
            queue.append((nxt, path))
    return None


def expand(store, names: Sequence[str], *, hops: int = 1,
           limit: int = 50) -> List[dict]:
    """Everything reachable within `hops` of these names, nearest first."""
    seeds = {(name or "").strip().lower() for name in names if (name or "").strip()}
    if not seeds:
        return []
    graph = _graph(store)
    found: Dict[str, dict] = {}
    frontier = [(seed, 0) for seed in sorted(seeds)]
    seen = set(seeds)
    while frontier:
        node, depth = frontier.pop(0)
        if depth >= hops:
            continue
        for hop in graph.get(node, []):
            nxt = hop["to"]
            if nxt in seen:
                continue
            seen.add(nxt)
            found[nxt] = {"name": nxt, "hops": depth + 1,
                          "via": node, "predicate": hop["predicate"],
                          "sentence": hop["sentence"]}
            frontier.append((nxt, depth + 1))
    ordered = sorted(found.values(), key=lambda item: (item["hops"], item["name"]))
    return ordered[:limit]


def render(path: Optional[dict]) -> str:
    """A path as a sentence, so it can be read rather than decoded."""
    if not path:
        return "no path"
    if not path["path"]:
        return f"{path['start']} is {path['goal']}"
    parts = [path["path"][0]["from"]]
    for hop in path["path"]:
        parts.append(f"-[{hop['predicate']}]-> {hop['to']}")
    return " ".join(parts) + f"  ({path['hops']} hop(s), " \
                             f"confidence {path['confidence']:.2f})"
