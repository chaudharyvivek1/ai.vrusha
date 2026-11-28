"""Two engines behind one interface, so retrieval never depends on one of them.

`vrusha` has a native index (`ann.py`): IVF over the store's own vectors, in
process, with SQLite as the truth. RagForge is a separate engine: JSON segments,
int8 and 1-bit codes, a filter language, an HTTP API. Neither is better at
everything, and which one is faster depends on things that change while the
program is running — corpus size, free RAM, whether a filter is needed, whether
the index is stale.

This module defines the seam. Each backend answers the same question:

    search(vector, k, text=..., filter=...) -> [(chunk_id, score)]

and declares what it can do. `router.py` picks one per query and explains why.

What a backend may NOT do is answer with text. Every hit is a chunk id, and the
citation, the licence and the address come back from SQLite afterwards. That is
deliberate: it is what keeps a second engine from becoming a second source of
truth.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .ann import AnnConfig, AnnError, AnnIndex, index_dir, load_for as _ann_load

Hit = Tuple[int, float]

#: What a backend can be asked to do. The router gates on these, so a query
#: that needs `filter` never lands on an engine that would ignore it.
VECTOR = "vector"
BM25 = "bm25"
FILTER = "filter"
SORT = "sort"
EXACT = "exact"
QUANTISED = "quantised"      # has int8/binary codes, so it scales past RAM
SERVER = "server"            # reachable from another language over HTTP


def _available_mb() -> Optional[float]:
    """Free physical memory, without adding a dependency."""
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return pages * size / (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return None


@dataclass
class BackendStats:
    name: str
    available: bool
    rows: int = 0
    dim: int = 0
    bytes: int = 0
    detail: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "available": self.available, "rows": self.rows,
                "dim": self.dim, "mb": round(self.bytes / 1e6, 1), **self.detail}


# ---------------------------------------------------------------------------
# the three backends
# ---------------------------------------------------------------------------


class Backend:
    """The seam. Every method must be cheap enough to call per query."""

    name = "abstract"
    capabilities: frozenset = frozenset()

    def available(self) -> bool:
        raise NotImplementedError

    def search(self, vector: np.ndarray, k: int, *, text: str = "",
               filter: Optional[dict] = None, nprobe: int = 0) -> List[Hit]:
        raise NotImplementedError

    def stats(self) -> BackendStats:
        raise NotImplementedError

    def can(self, *needs: str) -> bool:
        return all(need in self.capabilities for need in needs)


class ExactBackend(Backend):
    """The reference: read every vector, multiply, sort. Always right, never fast.

    It stays in the rotation because it is the only backend that cannot be
    stale, and because a fallback that is merely slow is worth more than one
    that is wrong.
    """

    name = "exact"
    capabilities = frozenset({VECTOR, EXACT})

    def __init__(self, store):
        self.store = store

    def available(self) -> bool:
        return self.store.vector_dim() is not None

    def search(self, vector, k, *, text="", filter=None, nprobe=0):
        ids, matrix = self.store.get_vectors()
        if not ids or matrix.shape[1] != vector.shape[0]:
            return []
        scores = matrix @ np.asarray(vector, dtype=np.float32)
        order = np.argsort(-scores)[:k]
        return [(int(ids[i]), float(scores[i])) for i in order]

    def stats(self):
        ids, matrix = self.store.get_vectors()
        return BackendStats(self.name, self.available(), rows=len(ids),
                            dim=int(matrix.shape[1]) if matrix.size else 0,
                            bytes=int(matrix.nbytes))


class NativeBackend(Backend):
    """The in-process index. Fast when fresh and when it fits in RAM."""

    name = "native"
    capabilities = frozenset({VECTOR, QUANTISED})

    def __init__(self, store, cfg: dict):
        self.store = store
        self.cfg = cfg

    def _index(self):
        return _ann_load(self.store, self.cfg)

    def available(self) -> bool:
        try:
            return self._index() is not None
        except Exception:
            return False

    def search(self, vector, k, *, text="", filter=None, nprobe=0):
        index = self._index()
        if index is None:
            raise AnnError("no fresh index")
        found = index.search(vector, k, store=self.store,
                             cfg=AnnConfig.from_cfg(self.cfg), nprobe=nprobe)
        return [(int(cid), float(score)) for cid, score in found]

    def stats(self):
        index = self._index()
        if index is None:
            # Not loadable through the gate, which refuses a stale index. Load it
            # ungated to tell "there is no index" from "it is out of date",
            # because those are different jobs for the operator.
            try:
                index = AnnIndex.load(index_dir(self.cfg) or "")
            except Exception:
                index = None
            if index is None:
                return BackendStats(self.name, False,
                                    detail={"stale": None, "index": "missing"})
            return BackendStats(self.name, False, rows=int(index.rows),
                                dim=int(index.dim),
                                bytes=int(index.stats()["bytes"]),
                                detail={"stale": index.is_stale(self.store),
                                        "index": "on disk"})
        stats = index.stats()
        return BackendStats(self.name, True, rows=int(stats["rows"]),
                            dim=int(stats["dim"]), bytes=int(stats["bytes"]),
                            detail={"cells": stats["cells"],
                                    "dtype": stats["dtype"],
                                    "mmapped": stats["mmapped"],
                                    "stale": index.is_stale(self.store)})


class RagForgeBackend(Backend):
    """RagForge over HTTP: a second engine, a second process, one interface.

    Zero dependencies on the Python side — `urllib` is enough, and the payload
    is JSON. Documents are addressed by `str(chunk_id)`, which is what lets a
    hit come back as a chunk id and be hydrated from SQLite like any other.

    It is not started by this class. `vrusha backends --start` will launch the
    Node process if it is on disk; otherwise the URL is assumed to be served by
    something the operator runs, which is how you would deploy it anyway.
    """

    name = "ragforge"
    capabilities = frozenset({VECTOR, BM25, FILTER, SORT, QUANTISED, SERVER})

    def __init__(self, url: str = "http://127.0.0.1:8080", timeout: float = 2.0,
                 token: str = "", dimension: int = 0):
        self.url = url.rstrip("/")
        self.timeout = float(timeout)
        self.token = token
        self.dimension = int(dimension)

    # -- transport ---------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        headers = {"content-type": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.url + path, data=body,
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str) -> dict:
        headers = {"authorization": f"Bearer {self.token}"} if self.token else {}
        request = urllib.request.Request(self.url + path, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    # -- the interface -----------------------------------------------------

    def available(self) -> bool:
        try:
            return bool(self._get("/ready").get("ready", True))
        except Exception:
            return False

    def search(self, vector, k, *, text="", filter=None, nprobe=0):
        payload: Dict[str, object] = {"k": int(k)}
        if vector is not None and len(vector):
            payload["vector"] = [float(x) for x in np.asarray(vector, dtype=np.float32)]
        if text:
            payload["text"] = text
        if filter:
            payload["filter"] = filter
        response = self._post("/search", payload)
        hits: List[Hit] = []
        for hit in response.get("hits", []):
            try:
                hits.append((int(hit["id"]), float(hit.get("score", 0.0))))
            except (KeyError, TypeError, ValueError):
                continue
        return hits

    def upsert(self, docs: Sequence[dict], vectors: np.ndarray,
               quantize: str = "int8") -> dict:
        """Push a batch. Used by `vrusha index export --to ragforge`."""
        return self._post("/upsert", {
            "docs": list(docs),
            "vectors": [[float(x) for x in row] for row in np.asarray(vectors, dtype=np.float32)],
            "quantize": quantize,
        })

    def stats(self):
        try:
            payload = self._get("/stats")
        except Exception:
            return BackendStats(self.name, False, detail={"url": self.url})
        return BackendStats(
            self.name, True,
            rows=int(payload.get("liveRows", payload.get("rows", 0)) or 0),
            dim=int(payload.get("dim", self.dimension) or 0),
            bytes=int(payload.get("bytes", 0) or 0),
            detail={"url": self.url,
                    "segments": payload.get("segments"),
                    "mode": payload.get("mode"),
                    "loadMs": payload.get("loadMs")},
        )


# ---------------------------------------------------------------------------
# construction from config
# ---------------------------------------------------------------------------


def build_backends(store, cfg: dict, embedder=None) -> Dict[str, Backend]:
    """Every backend this configuration can offer, available or not.

    Unavailable ones stay in the dict on purpose: `vrusha backends` should be
    able to tell you *why* RagForge is not being used, and a missing key cannot.
    """
    block = (cfg.get("backends", {}) or {})
    dimension = int(getattr(embedder, "dim", 0) or store.vector_dim() or 0)
    ragforge = block.get("ragforge", {}) or {}

    from .vec0 import Vec0Backend

    return {
        "exact": ExactBackend(store),
        "native": NativeBackend(store, cfg),
        # a third vector engine: same SQLite file family, no index format of its
        # own. Brute-force in C, which is fast below a million vectors and honest
        # about being slower above it.
        "sqlite-vec": Vec0Backend(store, cfg),
        "ragforge": RagForgeBackend(
            url=ragforge.get("url", "http://127.0.0.1:8080"),
            timeout=float(ragforge.get("timeout", 2.0)),
            token=os.environ.get(ragforge.get("token_env", "RAGFORGE_TOKEN"), ""),
            dimension=dimension,
        ),
    }


def free_ram_mb() -> Optional[float]:
    return _available_mb()
