"""Does the vector signal actually retrieve, now that it has semantics?

Every recall figure in docs/BENCHMARKS.md was measured with the `hash` embedder,
which is a feature hash and not a meaning. That makes those numbers a statement
about the *index* (does routing find the vector the scan would find) and not
about *retrieval* (is that vector the right document).

This measures the second thing, on a small labelled set: the repository's own
documentation, one topic per file, and queries phrased the way a person would
phrase them — deliberately avoiding the words the target document uses, because
a query that repeats the document's words tests BM25, not embeddings.

Three systems, same corpus, same queries:

    bm25        SQLite FTS5 over the document text
    hash        the previous default embedder
    model2vec   the static embedder adopted in wave 20

Reported: recall@1, recall@3, and MRR. The set is 15 documents and 12 queries —
small enough to check by hand, which is the point: a retrieval number nobody can
audit is a number nobody should trust.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from typing import Dict, List, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store.embed import HashEmbedder

DOCS = os.path.join(ROOT, "docs")

#: (query, the file that answers it). The query avoids the target's own words on
#: purpose: `car` against a document that says `automobile` is what an embedding
#: is for, and a query that repeats the title only measures the keyword index.
QUERIES: List[Tuple[str, str]] = [
    ("how do I stop the crawler from hammering a site", "OPERATIONS.md"),
    ("why are my results wrong after I added new pages", "ANN.md"),
    ("what happens when two pieces of text get the same number", "ADDRESS.md"),
    ("can this run on a laptop without a graphics card", "MODEL.md"),
    ("how do I know a page is allowed to be kept", "RULES.md"),
    ("splitting one long article into smaller pieces", "ARCHITECTURE.md"),
    ("teaching a small language model on my own text", "MODEL.md"),
    ("what am I allowed to do with the copied material", "RULES.md"),
    ("finding equations inside a research paper", "MATH.md"),
    ("answering a question and showing where it came from", "RESEARCH.md"),
    ("what do the numbers in the tree mean", "ADDRESS.md"),
    ("making search faster on a cheap machine", "ANN.md"),
]


def load_documents() -> Tuple[List[str], List[str]]:
    names, texts = [], []
    for name in sorted(os.listdir(DOCS)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(DOCS, name), encoding="utf-8") as handle:
            body = handle.read()
        if len(body) < 400:
            continue
        names.append(name)
        texts.append(body)
    return names, texts


def scores_from_vectors(vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
    return vectors @ query


def run_vector(embedder, names, texts, queries) -> Dict[str, float]:
    started = time.perf_counter()
    vectors = embedder.encode(texts)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.clip(norms, 1e-12, None)
    embed_seconds = time.perf_counter() - started

    hits = []
    for query, target in queries:
        query_vector = embedder.encode_one(query)
        query_vector = query_vector / max(1e-12, float(np.linalg.norm(query_vector)))
        ranking = np.argsort(-scores_from_vectors(vectors, query_vector))[:10]
        order = [names[index] for index in ranking]
        hits.append((order, target))
    return _metrics(hits, embed_seconds, len(texts))


def run_bm25(names, texts, queries) -> Dict[str, float]:
    started = time.perf_counter()
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE docs USING fts5(name, body)")
    connection.executemany("INSERT INTO docs (name, body) VALUES (?, ?)",
                           list(zip(names, texts)))
    build_seconds = time.perf_counter() - started

    hits = []
    for query, target in queries:
        # FTS5 cannot parse a bare question mark or a stray apostrophe
        safe = " ".join(token for token in
                        "".join(char if char.isalnum() else " "
                                for char in query).split() if token)
        # FTS5 reads a bare multi-word query as AND, so "how do I stop the
        # crawler" matches nothing. A search box means OR, ranked by bm25().
        expression = " OR ".join(f'"{token}"' for token in safe.split()) or '""'
        try:
            rows = connection.execute(
                "SELECT name FROM docs WHERE docs MATCH ? "
                "ORDER BY bm25(docs) LIMIT 3", (expression,)).fetchall()
            order = [row[0] for row in rows]
        except sqlite3.OperationalError:
            order = []
        hits.append((order, target))
    connection.close()
    return _metrics(hits, build_seconds, len(texts))


def chunk_documents(names, texts, size: int = 500):
    """Split each document into fixed-size pieces, remembering the parent.

    The first table treats one whole file as one vector, which dilutes it: a
    12,000-character document mean-pooled into 256 numbers is an average of
    everything it says. The pipeline chunks for exactly this reason, so the
    second table measures what the store actually stores.
    """
    pieces, parents = [], []
    for name, text in zip(names, texts):
        for start in range(0, len(text), size):
            piece = text[start:start + size].strip()
            if len(piece) >= 200:
                pieces.append(piece)
                parents.append(name)
    return pieces, parents


def run_chunked(embedder, names, texts, queries) -> Dict[str, float]:
    started = time.perf_counter()
    pieces, parents = chunk_documents(names, texts)
    vectors = embedder.encode(pieces)
    vectors = vectors / np.clip(np.linalg.norm(vectors, axis=1, keepdims=True),
                                1e-12, None)
    build = time.perf_counter() - started

    hits = []
    for query, target in queries:
        query_vector = embedder.encode_one(query)
        query_vector = query_vector / max(1e-12, float(np.linalg.norm(query_vector)))
        ranking = np.argsort(-(vectors @ query_vector))[:10]
        order = [parents[index] for index in ranking]
        hits.append((order, target))
    return _metrics(hits, build, len(pieces))


def run_bm25_chunked(names, texts, queries) -> Dict[str, float]:
    pieces, parents = chunk_documents(names, texts)
    started = time.perf_counter()
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE docs USING fts5(parent, body)")
    connection.executemany("INSERT INTO docs (parent, body) VALUES (?, ?)",
                           list(zip(parents, pieces)))
    build = time.perf_counter() - started
    hits = []
    for query, target in queries:
        safe = " ".join(token for token in
                        "".join(char if char.isalnum() else " "
                                for char in query).split() if token)
        expression = " OR ".join(f'"{token}"' for token in safe.split()) or '""'
        try:
            rows = connection.execute(
                "SELECT parent FROM docs WHERE docs MATCH ? "
                "ORDER BY bm25(docs) LIMIT 10", (expression,)).fetchall()
            order = [row[0] for row in rows]
        except sqlite3.OperationalError:
            order = []
        hits.append((order, target))
    connection.close()
    return _metrics(hits, build, len(pieces))


def fuse(first: Dict[str, float], second: Dict[str, float], k: int = 60,
         name: str = "fused") -> Dict[str, float]:
    """Reciprocal rank fusion, the same rule `retrieve()` uses.

    RRF is why the pipeline does not have to calibrate a cosine against a BM25
    score: a document that is first on both lists beats one that is first on
    only one, and neither raw scale is involved.
    """
    merged = []
    for (order_a, target), (order_b, _same) in zip(first["orders"],
                                                   second["orders"]):
        scores: Dict[str, float] = {}
        for ranking in (order_a, order_b):
            for rank, document in enumerate(ranking, start=1):
                scores[document] = scores.get(document, 0.0) + 1.0 / (k + rank)
        order = sorted(scores, key=lambda document: -scores[document])[:10]
        merged.append((order, target))
    return _metrics(merged, 0.0, first["corpus"])


def _metrics(hits, build_seconds: float, corpus: int) -> Dict[str, float]:
    # a list is unhashable, so the per-query record is the order itself
    orders = list(hits)
    at1 = sum(1 for order, target in hits if order[:1] == [target])
    at3 = sum(1 for order, target in hits if target in order[:3])
    reciprocal = 0.0
    for order, target in hits:
        if target in order:
            reciprocal += 1.0 / (order.index(target) + 1)
    count = max(1, len(hits))
    return {"recall@1": at1 / count, "recall@3": at3 / count,
            "mrr": reciprocal / count, "seconds": round(build_seconds, 2),
            "corpus": corpus, "queries": len(hits), "orders": orders}


def main() -> int:
    names, texts = load_documents()
    print(f"corpus   {len(names)} document(s) from docs/, "
          f"{sum(len(text) for text in texts):,} characters")
    print(f"queries  {len(QUERIES)} labelled by hand, phrased away from the "
          f"target's own words\n")

    systems: List[Tuple[str, Dict[str, float]]] = [
        ("bm25", run_bm25(names, texts, QUERIES)),
        ("hash", run_vector(HashEmbedder(256), names, texts, QUERIES)),
    ]
    try:
        from model2vec import StaticModel
        model = StaticModel.from_pretrained("minishlab/potion-base-8M")
        from vrusha.store.embed import Model2VecEmbedder
        systems.append(("model2vec", run_vector(Model2VecEmbedder.__new__(
            Model2VecEmbedder) if False else _wrap(model), names, texts, QUERIES)))
    except Exception as exc:                      # noqa: BLE001 - absent or no hub
        print(f"model2vec skipped: {type(exc).__name__}: {exc}\n")

    header = f"{'system':<12}{'recall@1':>10}{'recall@3':>10}{'MRR':>8}{'build s':>9}"
    print("one vector per document:")
    print(header)
    print("-" * len(header))
    for name, metrics in systems:
        print(f"{name:<12}{metrics['recall@1']:>9.0%}{metrics['recall@3']:>10.0%}"
              f"{metrics['mrr']:>8.3f}{metrics['seconds']:>9.2f}")

    # and at chunk granularity, which is what the store actually holds
    print()
    print("chunked (500 chars), a hit is a chunk from the right document:")
    print(f"{'system':<12}{'recall@1':>10}{'recall@3':>10}{'MRR':>8}{'chunks':>9}")
    print("-" * 49)
    chunked = [("bm25", run_bm25_chunked(names, texts, QUERIES))]
    chunked.append(("hash", run_chunked(HashEmbedder(256), names, texts, QUERIES)))
    try:
        from model2vec import StaticModel
        model = StaticModel.from_pretrained("minishlab/potion-base-8M")
        chunked.append(("model2vec", run_chunked(_wrap(model), names, texts, QUERIES)))
    except Exception:                        # noqa: BLE001 - already reported above
        pass
    for name, metrics in chunked:
        print(f"{name:<12}{metrics['recall@1']:>9.0%}{metrics['recall@3']:>10.0%}"
              f"{metrics['mrr']:>8.3f}{metrics['corpus']:>9,}")
    by_name = dict(chunked)
    if "hash" in by_name:
        mixed = fuse(by_name["bm25"], by_name["hash"], name="bm25+hash")
        chunked.append(("bm25+hash", mixed))
    if "model2vec" in by_name:
        mixed = fuse(by_name["bm25"], by_name["model2vec"], name="bm25+m2v")
        chunked.append(("bm25+m2v", mixed))
    for name, metrics in chunked[-2:]:
        print(f"{name:<12}{metrics['recall@1']:>9.0%}{metrics['recall@3']:>10.0%}"
              f"{metrics['mrr']:>8.3f}{'--':>9}")
    systems = chunked
    best_name, best_metrics = max(systems, key=lambda pair: pair[1]["mrr"])
    print(f"per query, {best_name} (audit the labels before trusting the table):")
    for (query, target), (order, _label) in zip(QUERIES, best_metrics["orders"]):
        rank = order.index(target) + 1 if target in order else 0
        mark = f"#{rank}" if rank else "miss"
        print(f"  {mark:<5} {target:<20} {query[:60]}")

    print()
    print("recall@1 is the share of queries whose top hit is the right document;")
    print("MRR is 1/rank averaged, so a miss at rank 1 costs more than a miss at 3.")
    return 0


class _wrap:
    """Adapt a bare StaticModel to the Embedder protocol for this script."""

    def __init__(self, model):
        self._model = model
        self.dim = int(getattr(model, "dim", 256))

    def encode(self, texts):
        vectors = np.asarray(self._model.encode(list(texts)), dtype=np.float32)
        return vectors

    def encode_one(self, text):
        return self.encode([text])[0]


if __name__ == "__main__":
    raise SystemExit(main())
