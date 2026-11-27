"""SQLite -> RagForge, so the same chunks can be served by either engine.

The store is the truth; RagForge is a second index over the same rows. Export
walks `iter_vectors` and pushes batches of `{docs, vectors}`. Each doc keeps the
fields that make a hit citable — url, title, licence, address — under `meta`, so
a hit that comes back through RagForge can be hydrated from SQLite and shown
with the same citation as one that came from the native index.

`id` is `str(chunk_id)`. That is the whole contract between the two engines.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

from .backends import RagForgeBackend


def doc_for(store, chunk_id: int) -> Optional[dict]:
    """The citable shape of one chunk. None if it is no longer active."""
    row = store.conn.execute(
        """SELECT c.chunk_id, c.text, c.address, c.kind, c.symbol, c.lang,
                  d.url, d.title, d.licence
           FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
           WHERE c.chunk_id = ?""", (chunk_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": str(int(row["chunk_id"])),
        "text": row["text"],
        "meta": {
            "url": row["url"],
            "title": row["title"],
            "licence": row["licence"],
            "address": row["address"],
            "kind": row["kind"],
            "symbol": row["symbol"],
            "lang": row["lang"],
        },
    }


def batches(store, size: int = 2000) -> Iterator[Tuple[List[dict], np.ndarray]]:
    """Stream (docs, vectors) in the order the store returns them."""
    size = max(1, int(size))
    for ids, matrix in store.iter_vectors(batch=size):
        docs: List[dict] = []
        keep: List[int] = []
        for offset, chunk_id in enumerate(ids):
            doc = doc_for(store, chunk_id)
            if doc is None:
                continue
            docs.append(doc)
            keep.append(offset)
        if docs:
            yield docs, matrix[keep]


def export_to_url(store, backend: RagForgeBackend, *, size: int = 2000,
                  quantize: str = "int8", verbose: bool = True) -> Dict[str, object]:
    """POST every batch. Returns a summary; raises on the first failed batch."""
    total_docs = 0
    total_batches = 0
    for docs, vectors in batches(store, size):
        backend.upsert(docs, vectors, quantize=quantize)
        total_docs += len(docs)
        total_batches += 1
        if verbose and total_batches % 10 == 0:
            print(f"  exported {total_docs:,} chunks in {total_batches} batches")
    return {"chunks": total_docs, "batches": total_batches, "url": backend.url}


def export_to_dir(store, directory: str, *, size: int = 2000,
                  verbose: bool = True) -> Dict[str, object]:
    """Write one JSON file per batch, for an `upsert` run by something else.

    Same payload as `export_to_url`, so a batch file can be posted verbatim:

        curl -X POST http://127.0.0.1:8080/upsert \\
             -H 'content-type: application/json' --data @batch-00000.json
    """
    os.makedirs(directory, exist_ok=True)
    total_docs = 0
    files = []
    for number, (docs, vectors) in enumerate(batches(store, size)):
        path = os.path.join(directory, f"batch-{number:05d}.json")
        payload = {"docs": docs,
                   "vectors": [[round(float(x), 6) for x in row] for row in vectors],
                   "quantize": "int8"}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        total_docs += len(docs)
        files.append(path)
        if verbose and number % 10 == 0:
            print(f"  wrote {path} ({len(docs):,} chunks)")
    manifest = {"dim": store.vector_dim(), "chunks": total_docs,
                "batches": len(files), "files": files}
    with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest


def export_to_jsonl(store, path: str, *, verbose: bool = True) -> Dict[str, object]:
    """Write the one-line-per-chunk format RagForge's own ingest reads.

        node dist/src/cli.js ingest --in corpus.jsonl --out data

    This is the seed path: it needs no running server, and it is the same shape
    their CLI documents. `{"id", "text", "vector", "meta"}` per line.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    total = 0
    with open(path, "w", encoding="utf-8") as handle:
        for docs, vectors in batches(store, size=5000):
            for doc, vector in zip(docs, vectors):
                row = {"id": doc["id"], "text": doc["text"],
                       "vector": [round(float(x), 6) for x in vector],
                       "meta": doc["meta"]}
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                total += 1
            if verbose:
                print(f"  wrote {total:,} chunks to {path}")
    return {"path": path, "chunks": total, "dim": store.vector_dim()}
