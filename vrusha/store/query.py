"""Retrieval: find the chunks that answer a question, and say where each came from.

Three signals, fused by reciprocal rank rather than by adding scores that are
not on the same scale:

    bm25      FTS5 over the chunk text. Exact words. Fails on synonyms.
    vector    cosine over embeddings. Synonyms. Fails on rare identifiers.
    address   proximity to an address in the query or to the best hit.

Fusing by rank is the trick that makes this work without tuning: a chunk that
is first on both lists beats a chunk that is first on one and tenth on the
other, and neither signal's raw scale has to be calibrated.

`query` shows you the retrieved chunks. `ask` then answers from them. Run
`query` first, always: if retrieval returns nothing useful, the answer will be
invented, and you will blame the model for a retrieval problem.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from .address import Address, centroid_table, semantic_similarity
from .ann import AnnConfig, AnnError, load_for as _ann_load
from .db import Store

_ADDRESS_IN_QUERY = re.compile(r"\b\d{1,2}(?:\.\d{1,8}){1,4}\b")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _ann(store: Store, cfg: Optional[dict]):
    """The usable index for this store, or None. Never raises.

    `load_for` decides: mode, freshness, size. Keeping the decision in one
    place means `query`, `ask` and the benchmark all agree about when the
    index is in play, which is what makes a recall number mean something.
    """
    return _ann_load(store, cfg)


@dataclass
class Hit:
    chunk_id: int
    address: Optional[Address]
    text: str
    symbol: str = ""
    kind: str = ""
    lang: str = ""
    url: str = ""
    title: str = ""
    licence: Optional[str] = None
    scores: Dict[str, float] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return self.scores.get("fused", 0.0)

    def citation(self) -> str:
        address = str(self.address) if self.address else "unaddressed"
        return f"{self.url} [{address}]"

    def to_dict(self, preview: int = 400) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "address": str(self.address) if self.address else None,
            "address_label": self.address.label() if self.address else None,
            "kind": self.kind,
            "symbol": self.symbol,
            "lang": self.lang,
            "url": self.url,
            "title": self.title,
            "licence": self.licence,
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "preview": re.sub(r"\s+", " ", self.text)[:preview],
        }


@dataclass
class Retrieval:
    query: str
    hits: List[Hit] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    address_hint: Optional[Address] = None
    #: which engine served the vector signal, and why (see store/router.py)
    decision: Optional[dict] = None

    @property
    def empty(self) -> bool:
        return not self.hits

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else 0.0

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "notes": self.notes,
            "decision": self.decision,
            "address_hint": str(self.address_hint) if self.address_hint else None,
            "hits": [h.to_dict() for h in self.hits],
        }


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------

def _reciprocal_rank_fusion(rankings: Dict[str, List[int]], weights: Dict[str, float],
                            k: int = 60) -> Dict[int, float]:
    fused: Dict[int, float] = {}
    for signal, ids in rankings.items():
        weight = weights.get(signal, 1.0)
        for rank, chunk_id in enumerate(ids, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + weight / (k + rank)
    return fused


def _vector_ranking(store: Store, embedder, query: str, limit: int,
                    floor: float = 0.0, cfg: Optional[dict] = None) -> List[tuple[int, float]]:
    """Cosine ranking, with a floor.

    Without the floor the vector signal always returns its top N, however
    unrelated, and retrieval is never empty. That is what makes a system
    answer a question it has no material for.

    The index answers first when there is a fresh one: it scores a few cells
    instead of every row. The full scan below stays, because it is both the
    fallback when no index exists and the reference the index is measured
    against. A floor that filters everything is not an error: the caller is
    told the vector signal found nothing, and fuses what is left.
    """
    query_vector = embedder.encode_one(query)

    index = _ann(store, cfg)
    if index is not None and query_vector.shape[0] == index.dim:
        try:
            ranked = index.search(query_vector, limit, store=store,
                                  cfg=AnnConfig.from_cfg(cfg))
            return [(chunk_id, score) for chunk_id, score in ranked if score >= floor]
        except AnnError:
            pass  # a broken index is a slow query, never a failed one

    ids, matrix = store.get_vectors()
    if not ids:
        return []
    if query_vector.shape[0] != matrix.shape[1]:
        return []
    similarity = matrix @ query_vector
    order = np.argsort(-similarity)[:limit]
    return [(ids[int(i)], float(similarity[int(i)])) for i in order
            if float(similarity[int(i)]) >= floor]


_ADDRESS_CACHE: Dict[str, tuple] = {}


def _address_table(store: Store):
    """Every chunk's address as integers, materialised once per revision.

    `semantic_similarity` is a Python function called once per chunk, and the
    address was parsed from a string first: at 200k chunks that was 64 ms of an
    82 ms query. The numeric columns already exist in `chunks`, so the whole
    ranking can be done in numpy — and the matrix is cached against
    `chunk_revision()`, which triggers maintain, so a write invalidates it
    without a scan.
    """
    key = str(store.path)
    revision = store.chunk_revision()
    cached = _ADDRESS_CACHE.get(key)
    if cached is not None and cached[0] == revision:
        return cached[1], cached[2], cached[3]

    rows = store.conn.execute(
        "SELECT chunk_id, addr_domain, addr_family, addr_kind, addr_cluster, "
        "addr_ordinal FROM chunks WHERE address IS NOT NULL AND active = 1 "
        "ORDER BY chunk_id").fetchall()
    count = len(rows)
    chunk_ids = np.fromiter((int(row[0]) for row in rows), dtype=np.int64,
                            count=count)
    matrix = np.zeros((count, 5), dtype=np.int32)
    for column in range(5):
        matrix[:, column] = np.fromiter(
            (int(row[column + 1] or 0) for row in rows), dtype=np.int32,
            count=count)
    centroids = centroid_table(store.get_centroids())
    _ADDRESS_CACHE[key] = (revision, chunk_ids, matrix, centroids)
    return chunk_ids, matrix, centroids


def _address_ranking(store: Store, hint: Address, limit: int) -> List[tuple[int, float]]:
    """Rank by meaning, not by subtracting segment numbers.

    Cluster ids are assigned by cluster *size* (`sort.stability`), so cluster 1
    and cluster 2 are the two biggest topics, not two neighbouring ones. The
    centroids are the only thing in the store that knows which clusters are
    actually about the same thing.

    Same rule as `semantic_similarity`, vectorised: labels mismatch by a fixed
    penalty, clusters compare by centroid cosine, and the ordinal only matters
    inside one cluster.
    """
    chunk_ids, matrix, centroids = _address_table(store)
    if len(chunk_ids) == 0:
        return []

    target = np.asarray(hint.segments[:5], dtype=np.int32)
    same_domain = matrix[:, 0] == target[0]
    same_family = matrix[:, 1] == target[1]
    same_kind = matrix[:, 2] == target[2]
    same_cluster = matrix[:, 3] == target[3]

    score = np.zeros(len(chunk_ids), dtype=np.float64)
    score[same_domain] = 0.25
    score[same_domain & same_family] = 0.5
    deep = same_domain & same_family & same_kind
    score[deep] = 0.6                       # different cluster, no centroids
    score[deep & same_cluster] = 1.0

    if centroids:
        hint_vector = centroids.get(tuple(int(part) for part in target[:4]))
        if hint_vector is not None:
            index = np.full(len(chunk_ids), -1, dtype=np.int32)
            table = []
            position: Dict[tuple, int] = {}
            for row, address in enumerate(map(tuple, matrix[deep][:, :4])):
                key = tuple(int(part) for part in address)
                if key not in position:
                    position[key] = len(table)
                    table.append(centroids.get(key))
                index[np.flatnonzero(deep)[row]] = position[key]
            usable = np.array([vector is not None for vector in table], dtype=bool)
            if usable.any():
                stacked = np.stack([vector if vector is not None
                                    else np.zeros_like(hint_vector, dtype=np.float32)
                                    for vector in table])
                cosine = stacked.astype(np.float64) @ np.asarray(
                    hint_vector, dtype=np.float64)
                cosine = np.clip(cosine, 0.0, 1.0)
                chosen = deep & (index >= 0)
                valid = np.zeros(len(chunk_ids), dtype=bool)
                valid[chosen] = usable[index[chosen]]
                score[valid] = cosine[index[valid]]

    # inside one cluster the ordinal is a weak position, not a distance
    ordinals = matrix[:, 4]
    if target[4]:
        damped = same_cluster & (ordinals != 0)
        score[damped] = score[damped] / (1.0 + 0.02 * np.abs(
            ordinals[damped] - target[4]))

    keep = np.flatnonzero(score > 0)
    if len(keep) == 0:
        return []
    order = keep[np.lexsort((chunk_ids[keep], -score[keep]))]
    return [(int(chunk_ids[i]), float(score[i])) for i in order[:limit]]


def _identifier_bonus(query: str, hit: Hit) -> float:
    """Code questions name identifiers. A chunk containing them is more likely right.

    Prose questions are made of ordinary words, so applying this there would
    boost whichever paragraph happens to share vocabulary with the question
    rather than the one that answers it.
    """
    if hit.kind not in ("module", "class", "function", "block"):
        return 0.0
    names = {n.lower() for n in _IDENTIFIER.findall(query)}
    if not names:
        return 0.0
    body = (hit.text + " " + hit.symbol).lower()
    hits = sum(1 for name in names if name in body)
    return hits / max(1, len(names))


# --------------------------------------------------------------------------
# the retriever
# --------------------------------------------------------------------------

def retrieve(store: Store, embedder, query: str, cfg: dict,
             address_hint: Optional[Address] = None,
             top_k: Optional[int] = None,
             router=None) -> Retrieval:
    """Retrieve. With a `router`, the vector signal is served by whichever
    engine the router picks (native, RagForge, exact) instead of always by
    `_vector_ranking`; everything downstream is identical, because a backend
    returns chunk ids and scores and nothing else."""
    query_cfg = cfg.get("query", {})
    top_k = int(top_k or query_cfg.get("top_k", 8))
    candidates = int(query_cfg.get("candidates", 60))
    weights = dict(query_cfg.get("fusion", {"bm25": 1.0, "vector": 1.0, "address": 0.35}))

    result = Retrieval(query=query, address_hint=address_hint)

    if address_hint is None:
        match = _ADDRESS_IN_QUERY.search(query)
        if match:
            try:
                address_hint = Address.parse(match.group(0))
                result.address_hint = address_hint
                result.notes.append(f"address hint {address_hint} taken from the query")
            except Exception:
                pass

    floor = float(query_cfg.get("min_vector_similarity", 0.05))
    stored_dim, embedder_dim = store.vector_dim(), getattr(embedder, "dim", None)
    if stored_dim and embedder_dim and int(stored_dim) != int(embedder_dim):
        result.notes.append(
            f"vector width mismatch: store {stored_dim}, embedder {embedder_dim} "
            f"-- the vector signal is disabled; re-embed or fix store.embed_dim")
    recorded = store.embedder_name()
    current = str(getattr(embedder, "name", "") or "")
    if recorded and current and recorded != current:
        result.notes.append(
            f"warning: this store's vectors came from {recorded!r} but this "
            f"query used {current!r}; the vector signal is comparing two "
            f"different spaces. Re-embed, or point the config at {recorded!r}.")

    bm25 = store.fts_search(query, limit=candidates)
    if router is not None:
        try:
            hits, decision, timings = router.search(
                embedder.encode_one(query), candidates, text=query)
            vector = [(cid, score) for cid, score in hits if score >= floor]
            result.decision = decision.to_dict()
            result.decision["ms"] = timings
            result.notes.append(f"vector via {decision}")
        except Exception as exc:            # noqa: BLE001
            result.notes.append(f"router failed ({exc}); falling back to the store")
            vector = _vector_ranking(store, embedder, query, candidates,
                                     floor=floor, cfg=cfg)
    else:
        vector = _vector_ranking(store, embedder, query, candidates, floor=floor, cfg=cfg)
    result.notes.append(f"bm25 {len(bm25)} candidates, vector {len(vector)} candidates")

    rankings = {
        "bm25": [chunk_id for chunk_id, _ in bm25],
        "vector": [chunk_id for chunk_id, _ in vector],
    }
    address_hits: List[tuple[int, float]] = []
    if address_hint is not None:
        address_hits = _address_ranking(store, address_hint, candidates)
        rankings["address"] = [chunk_id for chunk_id, _ in address_hits]
        result.notes.append(f"address {len(address_hits)} candidates near {address_hint}")

    # Two fusions, not one. `primary` is the evidence: the text matched, or the
    # meaning matched. `tiebreak` is where the address puts things. Ordering on
    # the sum of the two lets a region hint overturn a much better lexical hit,
    # which is how a tiebreaker stops being a tiebreaker. Order is
    # (has evidence, primary strength, total), so the address can only reorder
    # hits whose evidence is equal.
    primary = _reciprocal_rank_fusion(
        {k: rankings[k] for k in ("bm25", "vector") if k in rankings}, weights)
    tiebreak = (_reciprocal_rank_fusion({"address": rankings["address"]}, weights)
                if "address" in rankings else {})
    fused = {cid: primary.get(cid, 0.0) + tiebreak.get(cid, 0.0)
             for cid in set(primary) | set(tiebreak)}
    if not fused:
        return result

    bm25_map = dict(bm25)
    vector_map = dict(vector)
    order = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:candidates]

    hits: List[Hit] = []
    for chunk_id, fused_score in order:
        row = store.conn.execute(
            """SELECT c.*, d.url AS url, d.title AS title, d.licence AS licence
               FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
               WHERE c.chunk_id = ?""", (chunk_id,)).fetchone()
        if row is None or not row["active"]:
            continue
        address = Address.parse(row["address"]) if row["address"] else None
        hit = Hit(
            chunk_id=chunk_id,
            address=address,
            text=row["text"],
            symbol=row["symbol"] or "",
            kind=row["kind"],
            lang=row["lang"] or "",
            url=row["url"],
            title=row["title"] or "",
            licence=row["licence"],
            scores={
                "fused": fused_score,
                "primary": primary.get(chunk_id, 0.0),
                "bm25": bm25_map.get(chunk_id, 0.0),
                "vector": vector_map.get(chunk_id, 0.0),
            },
        )
        # a small, explainable rerank: identifiers in the query that appear here
        bonus = _identifier_bonus(query, hit)
        if bonus:
            hit.scores["identifier"] = bonus
            hit.scores["fused"] = fused_score * (1.0 + 0.25 * bonus)
        hits.append(hit)

    # Evidence first, address second. A hit the address signal produced with no
    # lexical or semantic support sorts below every hit that has some, and
    # among supported hits the primary score decides -- the address only breaks
    # a genuine tie.
    def _rank(hit: "Hit"):
        supported = hit.scores.get("primary", 0.0) > 0
        return (0 if supported else 1, -hit.scores.get("primary", 0.0), -hit.score,
                hit.chunk_id)

    hits.sort(key=_rank)
    result.hits = hits[:top_k]
    return result


# --------------------------------------------------------------------------
# context and prompt
# --------------------------------------------------------------------------

def build_context(hits: Sequence[Hit], max_chars: int = 8000) -> str:
    """Numbered, cited blocks. The model is told to use these numbers."""
    blocks: List[str] = []
    used = 0
    for index, hit in enumerate(hits, start=1):
        header = f"[{index}] {hit.title or hit.url} — {hit.citation()}"
        body = hit.text.strip()
        if used + len(header) + len(body) > max_chars:
            remaining = max_chars - used - len(header)
            if remaining < 200:
                break
            body = body[:remaining].rstrip() + " …"
        blocks.append(f"{header}\n{body}")
        used += len(header) + len(body) + 2
    return "\n\n".join(blocks)


SYSTEM_PROMPT = (
    "You answer only from the numbered context. Cite the number after each "
    "claim, like [2]. If the context does not contain the answer, say so "
    "plainly and do not guess."
)


def render_prompt(question: str, context: str) -> List[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]
