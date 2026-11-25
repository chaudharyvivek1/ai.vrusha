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

from .address import Address
from .db import Store

_ADDRESS_IN_QUERY = re.compile(r"\b\d{1,2}(?:\.\d{1,8}){1,4}\b")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


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
                    floor: float = 0.0) -> List[tuple[int, float]]:
    """Cosine ranking, with a floor.

    Without the floor the vector signal always returns its top N, however
    unrelated, and retrieval is never empty. That is what makes a system
    answer a question it has no material for.
    """
    ids, matrix = store.get_vectors()
    if not ids:
        return []
    query_vector = embedder.encode_one(query)
    if query_vector.shape[0] != matrix.shape[1]:
        return []
    similarity = matrix @ query_vector
    order = np.argsort(-similarity)[:limit]
    return [(ids[int(i)], float(similarity[int(i)])) for i in order
            if float(similarity[int(i)]) >= floor]


def _address_ranking(store: Store, hint: Address, limit: int) -> List[tuple[int, float]]:
    scored = []
    for chunk_id, address in store.addresses():
        distance = hint.distance(address)
        scored.append((chunk_id, 1.0 / (1.0 + distance)))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:limit]


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
             top_k: Optional[int] = None) -> Retrieval:
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
    bm25 = store.fts_search(query, limit=candidates)
    vector = _vector_ranking(store, embedder, query, candidates, floor=floor)
    result.notes.append(f"bm25 {len(bm25)} candidates, vector {len(vector)} candidates")

    rankings = {
        "bm25": [chunk_id for chunk_id, _ in bm25],
        "vector": [chunk_id for chunk_id, _ in vector],
    }
    if address_hint is not None:
        address_hits = _address_ranking(store, address_hint, candidates)
        rankings["address"] = [chunk_id for chunk_id, _ in address_hits]
        result.notes.append(f"address {len(address_hits)} candidates near {address_hint}")

    fused = _reciprocal_rank_fusion(rankings, weights)
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

    hits.sort(key=lambda h: (-h.score, h.chunk_id))
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
