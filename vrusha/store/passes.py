"""The passes: deterministic operators that reshape the store.

This is the part you described as the equations. Each pass is a small function
with a name, a rule level, a job and a measurable effect. They run in a fixed
order and each one writes what it did to the `passes` table, so a run is
reproducible and a change is visible.

    pass                      level  job
    ------------------------  -----  ------------------------------------------
    filter.policy               1    deactivate anything the licence rules refuse
    filter.quality              6    deactivate fragments and boilerplate
    filter.stale                3    flag material older than the stale window
    merge.exact                 7    identical text: keep one, link the rest
    merge.near_duplicate        7    cosine >= threshold: keep one, link the rest
    analyse.cluster             5    k-means per (domain, family, kind)
    analyse.centroid            5    the centre of each cluster
    sort.stability              5    order clusters by size, biggest first
    assign.address              5    write the five-segment address
    reflow.address              5    compact ordinals, log every change
    analyse.graph               8    top-k nearest neighbours as edges
    link.parent                 5    every chunk points at its parent region

Nothing here is deleted. A merged or filtered chunk is deactivated and keeps
its row, its text and its address history. That is what makes the pass log
worth having: you can always see what was removed and why.

Adding a pass: write the function, decorate it with `@pass_`, add its id to
`passes.order` in the config. Twelve ship today; the registry is the extension
point, not a fixed list.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

from .address import CODE_KIND_IDS, DOMAIN_IDS, KIND_IDS, Address
from .db import Store


@dataclass
class PassContext:
    store: Store
    cfg: dict
    rules: object = None
    registry: object = None
    verbose: bool = False

    @property
    def seed(self) -> int:
        return int(self.cfg.get("passes", {}).get("seed", 1234))


@dataclass
class Pass:
    id: str
    level: int
    description: str
    fn: Callable[[PassContext], dict]

    def run(self, ctx: PassContext) -> dict:
        stats = self.fn(ctx) or {}
        stats.setdefault("pass", self.id)
        stats.setdefault("level", self.level)
        ctx.store.log_pass(self.id, stats)
        ctx.store.commit()
        return stats


REGISTRY: Dict[str, Pass] = {}


def pass_(pass_id: str, level: int, description: str):
    """Register a pass. The id is what `passes.order` refers to."""
    def wrapper(fn: Callable[[PassContext], dict]) -> Callable[[PassContext], dict]:
        REGISTRY[pass_id] = Pass(pass_id, level, description, fn)
        return fn
    return wrapper


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _chunks_with_docs(store: Store, active_only: bool = True) -> List:
    query = """
        SELECT c.chunk_id, c.doc_id, c.kind, c.symbol, c.lang, c.text, c.chars,
               c.sha, c.address, c.addr_cluster, c.quality, c.created,
               d.licence AS doc_licence, d.domain AS doc_domain,
               d.kind AS doc_kind, d.lang AS doc_lang, d.retrieved AS doc_retrieved
        FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
    """
    if active_only:
        query += " WHERE c.active = 1"
    query += " ORDER BY c.chunk_id"
    return store.conn.execute(query).fetchall()


def _domain_id(row) -> int:
    kind = (row["doc_kind"] or "prose").lower()
    return {"code": DOMAIN_IDS["code"], "data": DOMAIN_IDS["data"],
            "rules": DOMAIN_IDS["rules"], "talk": DOMAIN_IDS["talk"]}.get(
                kind, DOMAIN_IDS["prose"])


def _kind_id(row, domain: int) -> int:
    table = CODE_KIND_IDS if domain == DOMAIN_IDS["code"] else KIND_IDS
    return table.get((row["kind"] or "").lower(), 1)


def _family_id(row, registry) -> int:
    code = row["lang"] or row["doc_lang"] or ""
    if registry is None:
        return 0
    return registry.family_id(code) if code else 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------

@pass_("filter.policy", 1, "deactivate chunks whose licence or domain the level-1 rules refuse")
def filter_policy(ctx: PassContext) -> dict:
    rules = ctx.rules
    blocked = {str(x).lower() for x in (rules.get("licence.blocked", default=[]) or [])}
    refused = [str(x).lower() for x in (rules.get("policy.refused_domains", default=[]) or [])]
    removed = 0
    by_reason: Dict[str, int] = {}
    for row in _chunks_with_docs(ctx.store):
        licence = (row["doc_licence"] or "").lower()
        domain = (row["doc_domain"] or "").lower()
        reason = None
        if licence and licence in blocked and licence != "unknown":
            reason = f"licence:{licence}"
        elif any(domain == r or domain.endswith("." + r) for r in refused):
            reason = f"domain:{domain}"
        if reason:
            ctx.store.deactivate(int(row["chunk_id"]), 0, weight=0.0)
            ctx.store.add_edge(int(row["chunk_id"]), 0, "filtered", 0.0)
            by_reason[reason] = by_reason.get(reason, 0) + 1
            removed += 1
    return {"deactivated": removed, "reasons": by_reason}


@pass_("filter.quality", 6, "deactivate fragments below the minimum size")
def filter_quality(ctx: PassContext) -> dict:
    minimum = int(ctx.rules.get("chunk.min_chars", default=80) or 80)
    removed = 0
    for row in _chunks_with_docs(ctx.store):
        text = row["text"] or ""
        if len(text.strip()) < minimum or len(re.findall(r"\w+", text)) < 8:
            ctx.store.deactivate(int(row["chunk_id"]), 0, weight=0.0)
            ctx.store.add_edge(int(row["chunk_id"]), 0, "filtered", 0.0)
            removed += 1
    return {"deactivated": removed, "min_chars": minimum}


@pass_("filter.stale", 3, "flag material older than the stale window")
def filter_stale(ctx: PassContext) -> dict:
    days = int(ctx.cfg.get("passes", {}).get("stale_days", 180))
    cutoff = _now() - timedelta(days=days)
    flagged = 0
    oldest = None
    for row in _chunks_with_docs(ctx.store):
        retrieved = _parse_ts(row["doc_retrieved"])
        if retrieved is None:
            continue
        if oldest is None or retrieved < oldest:
            oldest = retrieved
        if retrieved < cutoff:
            ctx.store.add_edge(int(row["chunk_id"]), 0, "stale", 1.0)
            flagged += 1
    return {"flagged": flagged, "window_days": days,
            "oldest": oldest.strftime("%Y-%m-%d") if oldest else None}


# --------------------------------------------------------------------------
# merges
# --------------------------------------------------------------------------

@pass_("merge.exact", 7, "identical text: keep the first, link the rest")
def merge_exact(ctx: PassContext) -> dict:
    seen: Dict[str, int] = {}
    merged = 0
    for row in _chunks_with_docs(ctx.store):
        key = row["sha"] or ""
        if not key:
            continue
        chunk_id = int(row["chunk_id"])
        if key in seen:
            ctx.store.deactivate(chunk_id, seen[key])
            merged += 1
        else:
            seen[key] = chunk_id
    return {"merged": merged, "unique": len(seen)}


@pass_("merge.near_duplicate", 7, "cosine above threshold: keep the first, link the rest")
def merge_near_duplicate(ctx: PassContext) -> dict:
    threshold = float(ctx.cfg.get("passes", {}).get("near_duplicate_threshold", 0.97))
    cross_family = bool(ctx.rules.get("dedup.cross_family", default=False) or False)
    rows = _chunks_with_docs(ctx.store)
    if not rows:
        return {"merged": 0}
    ids = [int(r["chunk_id"]) for r in rows]
    vec_ids, matrix = ctx.store.get_vectors(ids)
    if not vec_ids:
        return {"merged": 0, "note": "no vectors"}
    position = {chunk_id: i for i, chunk_id in enumerate(vec_ids)}
    family = {int(r["chunk_id"]): _family_id(r, ctx.registry) for r in rows}
    kept: List[int] = []
    merged = 0
    for chunk_id in vec_ids:
        index = position[chunk_id]
        duplicate_of = None
        for other in kept:
            if not cross_family and family.get(chunk_id) != family.get(other):
                continue
            score = float(np.dot(matrix[index], matrix[position[other]]))
            if score >= threshold:
                duplicate_of = other
                break
        if duplicate_of is not None:
            ctx.store.deactivate(chunk_id, duplicate_of, weight=1.0)
            merged += 1
        else:
            kept.append(chunk_id)
    return {"merged": merged, "kept": len(kept), "threshold": threshold}


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def _kmeans(matrix: np.ndarray, k: int, iters: int, seed: int) -> np.ndarray:
    """Deterministic k-means++ on normalised vectors. Returns a label per row."""
    n = matrix.shape[0]
    if n == 0 or k <= 1:
        return np.zeros(n, dtype=np.int64)
    k = min(k, n)
    rng = np.random.default_rng(seed)
    centres = np.empty((k, matrix.shape[1]), dtype=np.float32)
    centres[0] = matrix[0]
    distances = 1.0 - matrix @ centres[0]
    for i in range(1, k):
        probabilities = np.clip(distances, 0, None) ** 2
        total = probabilities.sum()
        if total <= 0:
            centres[i] = matrix[rng.integers(0, n)]
        else:
            centres[i] = matrix[int(rng.choice(n, p=probabilities / total))]
        distances = np.minimum(distances, 1.0 - matrix @ centres[i])
    labels = np.zeros(n, dtype=np.int64)
    for _ in range(max(1, iters)):
        similarity = matrix @ centres.T
        new_labels = np.argmax(similarity, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for i in range(k):
            members = matrix[labels == i]
            if len(members):
                centre = members.mean(axis=0)
                norm = np.linalg.norm(centre)
                centres[i] = centre / norm if norm else centre
    return labels


@pass_("analyse.cluster", 5, "k-means per (domain, family, kind): what is this about")
def analyse_cluster(ctx: PassContext) -> dict:
    rows = _chunks_with_docs(ctx.store)
    if not rows:
        return {"groups": 0}
    ids = [int(r["chunk_id"]) for r in rows]
    vec_ids, matrix = ctx.store.get_vectors(ids)
    position = {chunk_id: i for i, chunk_id in enumerate(vec_ids)}
    max_k = int(ctx.cfg.get("passes", {}).get("cluster_max", 24))
    iters = int(ctx.cfg.get("passes", {}).get("cluster_iters", 12))

    groups: Dict[tuple, List[int]] = {}
    meta: Dict[int, tuple] = {}
    for row in rows:
        chunk_id = int(row["chunk_id"])
        domain = _domain_id(row)
        family = _family_id(row, ctx.registry)
        kind = _kind_id(row, domain)
        groups.setdefault((domain, family, kind), []).append(chunk_id)
        meta[chunk_id] = (domain, family, kind)

    clustered = 0
    for (domain, family, kind), members in sorted(groups.items()):
        usable = [c for c in members if c in position]
        if len(usable) < 3 or matrix.size == 0:
            for chunk_id in members:
                ctx.store.conn.execute(
                    "UPDATE chunks SET addr_domain=?, addr_family=?, addr_kind=?, addr_cluster=? "
                    "WHERE chunk_id=?", (domain, family, kind, 1, chunk_id))
            continue
        sub = matrix[[position[c] for c in usable]]
        k = max(2, min(max_k, int(round(math.sqrt(len(usable) / 2)))))
        labels = _kmeans(sub, k, iters, ctx.seed)
        for chunk_id, label in zip(usable, labels):
            ctx.store.conn.execute(
                "UPDATE chunks SET addr_domain=?, addr_family=?, addr_kind=?, addr_cluster=? "
                "WHERE chunk_id=?", (domain, family, kind, int(label) + 1, chunk_id))
            clustered += 1
        for chunk_id in members:
            if chunk_id not in position:
                ctx.store.conn.execute(
                    "UPDATE chunks SET addr_domain=?, addr_family=?, addr_kind=?, addr_cluster=? "
                    "WHERE chunk_id=?", (domain, family, kind, 1, chunk_id))
    return {"groups": len(groups), "clustered": clustered}


@pass_("analyse.centroid", 5, "store the centre of every cluster")
def analyse_centroid(ctx: PassContext) -> dict:
    rows = ctx.store.conn.execute(
        "SELECT chunk_id, addr_domain, addr_family, addr_kind, addr_cluster FROM chunks "
        "WHERE active=1 AND addr_cluster IS NOT NULL").fetchall()
    if not rows:
        return {"centroids": 0}
    ids = [int(r["chunk_id"]) for r in rows]
    vec_ids, matrix = ctx.store.get_vectors(ids)
    position = {chunk_id: i for i, chunk_id in enumerate(vec_ids)}
    buckets: Dict[tuple, List[int]] = {}
    for row in rows:
        key = (row["addr_domain"], row["addr_family"], row["addr_kind"], row["addr_cluster"])
        buckets.setdefault(key, []).append(int(row["chunk_id"]))
    written = 0
    for (domain, family, kind, cluster), members in buckets.items():
        usable = [c for c in members if c in position]
        if not usable:
            continue
        centre = matrix[[position[c] for c in usable]].mean(axis=0)
        norm = np.linalg.norm(centre)
        if norm:
            centre = centre / norm
        ctx.store.set_centroid(int(domain), int(family), int(kind), int(cluster),
                               centre.astype(np.float32), len(usable))
        written += 1
    return {"centroids": written, "clusters": len(buckets)}


@pass_("sort.stability", 5, "order clusters by size so the biggest gets cluster 1")
def sort_stability(ctx: PassContext) -> dict:
    rows = ctx.store.conn.execute(
        "SELECT addr_domain, addr_family, addr_kind, addr_cluster, COUNT(*) AS n, "
        "SUM(chars) AS chars FROM chunks WHERE active=1 AND addr_cluster IS NOT NULL "
        "GROUP BY addr_domain, addr_family, addr_kind, addr_cluster").fetchall()
    order: Dict[str, int] = {}
    groups: Dict[tuple, List[tuple]] = {}
    for row in rows:
        key = (row["addr_domain"], row["addr_family"], row["addr_kind"])
        groups.setdefault(key, []).append(
            (int(row["addr_cluster"]), int(row["n"]), int(row["chars"] or 0)))
    for key, members in groups.items():
        # biggest first; ties broken by cluster id so the order never wobbles
        members.sort(key=lambda m: (-m[1], -m[2], m[0]))
        for rank, (cluster, _n, _chars) in enumerate(members, start=1):
            order[f"{key[0]}:{key[1]}:{key[2]}:{cluster}"] = rank
    ctx.store.conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('sort.stability', ?)",
        (json.dumps(order),))
    return {"clusters": len(order), "groups": len(groups)}


# --------------------------------------------------------------------------
# addressing
# --------------------------------------------------------------------------

@pass_("assign.address", 5, "write domain.family.kind.cluster.ordinal on every chunk")
def assign_address(ctx: PassContext) -> dict:
    row = ctx.store.conn.execute(
        "SELECT value FROM meta WHERE key='sort.stability'").fetchone()
    rank: Dict[str, int] = json.loads(row[0]) if row else {}

    rows = ctx.store.conn.execute(
        "SELECT chunk_id, addr_domain, addr_family, addr_kind, addr_cluster, chars "
        "FROM chunks WHERE active=1 ORDER BY addr_domain, addr_family, addr_kind, "
        "addr_cluster, chars DESC, chunk_id").fetchall()
    counters: Dict[tuple, int] = {}
    written = 0
    for r in rows:
        domain = int(r["addr_domain"] or 1)
        family = int(r["addr_family"] or 0)
        kind = int(r["addr_kind"] or 1)
        raw_cluster = int(r["addr_cluster"] or 1)
        cluster = rank.get(f"{domain}:{family}:{kind}:{raw_cluster}", raw_cluster)
        key = (domain, family, kind, cluster)
        counters[key] = counters.get(key, 0) + 1
        ctx.store.set_address(int(r["chunk_id"]),
                              Address((domain, family, kind, cluster, counters[key])),
                              "assign.address")
        written += 1
    return {"addressed": written, "regions": len(counters)}


@pass_("reflow.address", 5, "compact ordinals after merges so no number is skipped")
def reflow_address(ctx: PassContext) -> dict:
    rows = ctx.store.conn.execute(
        "SELECT chunk_id, addr_domain, addr_family, addr_kind, addr_cluster "
        "FROM chunks WHERE active=1 AND address IS NOT NULL "
        "ORDER BY addr_domain, addr_family, addr_kind, addr_cluster, addr_ordinal, chunk_id"
    ).fetchall()
    counters: Dict[tuple, int] = {}
    moved = 0
    for r in rows:
        key = (r["addr_domain"], r["addr_family"], r["addr_kind"], r["addr_cluster"])
        counters[key] = counters.get(key, 0) + 1
        address = Address((int(key[0]), int(key[1]), int(key[2]), int(key[3]), counters[key]))
        current = ctx.store.address_of(int(r["chunk_id"]))
        if current != address:
            ctx.store.set_address(int(r["chunk_id"]), address, "reflow.address")
            moved += 1
    return {"moved": moved, "regions": len(counters)}


@pass_("analyse.graph", 8, "connect every chunk to its nearest neighbours")
def analyse_graph(ctx: PassContext) -> dict:
    k = int(ctx.cfg.get("passes", {}).get("graph_neighbours", 5))
    rows = ctx.store.conn.execute(
        "SELECT chunk_id FROM chunks WHERE active=1 ORDER BY chunk_id").fetchall()
    ids = [int(r["chunk_id"]) for r in rows]
    vec_ids, matrix = ctx.store.get_vectors(ids)
    if not vec_ids:
        return {"edges": 0, "note": "no vectors"}
    edges = 0
    for index, chunk_id in enumerate(vec_ids):
        similarity = matrix @ matrix[index]
        order = np.argsort(-similarity)
        added = 0
        for neighbour_index in order:
            if neighbour_index == index:
                continue
            score = float(similarity[neighbour_index])
            if score <= 0:
                break
            ctx.store.add_edge(chunk_id, vec_ids[int(neighbour_index)], "near", round(score, 4))
            added += 1
            if added >= k:
                break
        edges += added
    return {"edges": edges, "k": k, "chunks": len(vec_ids)}


@pass_("link.parent", 5, "point every chunk at the representative of its parent region")
def link_parent(ctx: PassContext) -> dict:
    rows = ctx.store.conn.execute(
        "SELECT chunk_id, address FROM chunks WHERE active=1 AND address IS NOT NULL "
        "ORDER BY address").fetchall()
    by_prefix: Dict[str, int] = {}
    linked = 0
    for r in rows:
        address = Address.parse(r["address"])
        prefix = str(address.parent())
        representative = by_prefix.get(prefix)
        if representative is None:
            by_prefix[prefix] = int(r["chunk_id"])
        elif representative != int(r["chunk_id"]):
            ctx.store.add_edge(int(r["chunk_id"]), representative, "child_of", 1.0)
            linked += 1
    return {"linked": linked, "regions": len(by_prefix)}


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------

def available() -> List[str]:
    return sorted(REGISTRY)


def describe() -> List[dict]:
    return [
        {"id": p.id, "level": p.level, "description": p.description}
        for p in sorted(REGISTRY.values(), key=lambda p: (p.level, p.id))
    ]


def run_passes(ctx: PassContext, order: Optional[Sequence[str]] = None,
               only: Optional[Sequence[str]] = None) -> List[dict]:
    """Run the configured order. `only` runs a subset, still in that order."""
    order = list(order or ctx.cfg.get("passes", {}).get("order", []))
    unknown = [p for p in order if p not in REGISTRY]
    if unknown:
        raise KeyError(f"unknown pass(es) {unknown}; available: {available()}")
    results: List[dict] = []
    for pass_id in order:
        if only and pass_id not in only:
            continue
        result = REGISTRY[pass_id].run(ctx)
        results.append(result)
        if ctx.verbose:
            detail = ", ".join(f"{k}={v}" for k, v in result.items()
                               if k not in ("pass", "level"))
            print(f"  {pass_id:<22} {detail}")
    return results
