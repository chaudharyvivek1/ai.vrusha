"""The vector index: segmented IVF over int8, with an exact float32 rescore.

`query.py` fuses three signals. Two of them are cheap: FTS5 answers BM25 from
an index, and the address ranking compares a handful of integers. The third
one was not cheap. `_vector_ranking` used to read every vector out of SQLite
and multiply the whole matrix by the query, on every question, which is fine
for ten thousand chunks and unusable at ten million.

This module is the port of the RagForge design into the store, so the Python
package keeps one process, one install and no Node runtime:

    build    train centroids on a sample, assign every row to a cell,
             quantise to int8 with a per-row scale, write CSR by cell
    search   score centroids -> open `nprobe` cells -> dequantise just those
             rows -> float32 dot -> rescore the top candidates exactly

The two ideas that make it fast are the same two RagForge uses:

  * **Route before you scan.** Scoring 256 centroids costs nothing next to
    scoring 10 million vectors, and a good cell holds the answer.
  * **int8 on disk, float32 on the shortlist.** The quantised matrix is 4x
    smaller than float32, so it fits in RAM or pages in from mmap; only the
    handful of candidates that could actually win are read back at full
    precision from SQLite.

What is deliberately different from RagForge:

  * **SQLite is still the source of truth.** The index holds chunk ids and
    nothing else: no text, no licence, no address. Delete the index directory
    and no data is lost, which is why `index drop` is a safe command.
  * **The index can be stale, and it says so.** It stores the (count,
    max chunk id) it was built from. If the store has moved on, `load_for`
    returns nothing and retrieval falls back to the exact scan rather than
    answering from a snapshot that no longer matches the corpus.

Layout of the directory (all little-endian, no pickle, no code):

    meta.json       version, dim, rows, cells, dtype, fingerprint, build stats
    centroids.f32   (cells, dim) float32, L2-normalised
    ptr.i32         (cells + 1,) CSR offsets: the rows of cell c are the
                    contiguous slice ptr[c] .. ptr[c+1] of every other file
    rows.i32        (rows,) chunk_id for each internal row
    vec.i8 | vec.f32 (rows, dim) unit length, quantised or not — `dtype` in
                    meta says which; int8 is 4x smaller, float32 needs no
                    conversion at query time and is faster when it fits
    scale.f32       (rows,) the per-row dequantisation scale (1.0 for float32)
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np

VERSION = 1
META = "meta.json"
CENTROIDS = "centroids.f32"
PTR = "ptr.i32"
ROWS = "rows.i32"
VEC_I8 = "vec.i8"
VEC_F32 = "vec.f32"
SCALE = "scale.f32"


class AnnError(RuntimeError):
    """The index cannot be built, loaded or used, and the caller should know why."""


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------


def _normalise(matrix: np.ndarray) -> np.ndarray:
    """Rows to unit length. A zero row stays zero rather than becoming NaN."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (matrix / norms).astype(np.float32, copy=False)


def _quantise(matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """int8 plus a per-row scale, so a short vector keeps its precision."""
    peak = np.abs(matrix).max(axis=1)
    peak[peak == 0.0] = 1.0
    scale = (peak / 127.0).astype(np.float32)
    quantised = np.rint(matrix / scale[:, None]).clip(-127, 127).astype(np.int8)
    return quantised, scale


def _dequantise(rows: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return rows.astype(np.float32) * scale[:, None]


def _kmeans(matrix: np.ndarray, k: int, iters: int, seed: int) -> np.ndarray:
    """k-means++ init, then Lloyd. Cosine, because the vectors are normalised.

    The sample is already unit length, so a dot product is a cosine and the
    centroid of a cell only needs renormalising once at the end.
    """
    n = matrix.shape[0]
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)

    # k-means++: the first centre is random, every next one is drawn with
    # probability proportional to the squared distance from the nearest
    # centre already chosen. Plain random init collapses cells on clustered
    # data, which is exactly the shape a corpus has.
    centres = np.empty((k, matrix.shape[1]), dtype=np.float32)
    centres[0] = matrix[rng.integers(n)]
    closest = 1.0 - matrix @ centres[0]
    for index in range(1, k):
        weights = np.clip(closest, 0.0, None) ** 2
        total = float(weights.sum())
        if total <= 0.0:
            centres[index] = matrix[rng.integers(n)]
        else:
            pick = int(np.searchsorted(np.cumsum(weights) / total, rng.random()))
            centres[index] = matrix[min(pick, n - 1)]
        closest = np.minimum(closest, 1.0 - matrix @ centres[index])

    for _ in range(max(1, iters)):
        labels = _assign(matrix, centres)
        moved = np.zeros_like(centres)
        counts = np.bincount(labels, minlength=k)
        np.add.at(moved, labels, matrix)
        empty = counts == 0
        counts[empty] = 1
        centres = moved / counts[:, None]
        if empty.any():  # a cell that lost every row keeps its old centre
            centres[empty] = np.nan_to_num(centres[empty], nan=0.0)
        centres = _normalise(centres)
    return centres


def _assign(matrix: np.ndarray, centres: np.ndarray, batch: int = 20000) -> np.ndarray:
    """Nearest centre per row, in batches so the temporary stays small."""
    labels = np.empty(matrix.shape[0], dtype=np.int32)
    for start in range(0, matrix.shape[0], batch):
        block = matrix[start:start + batch]
        labels[start:start + batch] = np.argmax(block @ centres.T, axis=1)
    return labels


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


@dataclass
class AnnConfig:
    """Everything the index needs to know, with the defaults from `config.py`."""

    target_cell_size: int = 512
    train_sample: int = 100_000
    iters: int = 10
    seed: int = 13
    nprobe: int = 0          # 0 -> derived from the cell count
    rescore: int = 128       # candidates read back at float32
    ram_limit_mb: int = 512  # below this the matrix is held in RAM, above it mmapped
    #: int8    4x smaller, one conversion per scored row at query time
    #: float32 no conversion, so it is measurably faster when it fits in RAM
    #: auto    float32 when the matrix fits under ram_limit_mb, else int8
    dtype: str = "auto"

    @classmethod
    def from_cfg(cls, cfg: Optional[dict]) -> "AnnConfig":
        block = (cfg or {}).get("ann", {}) if cfg else {}
        base = cls()
        for name in ("target_cell_size", "train_sample", "iters", "seed",
                     "nprobe", "rescore", "ram_limit_mb"):
            if name in block:
                setattr(base, name, int(block[name]))
        if block.get("dtype"):
            base.dtype = str(block["dtype"]).lower()
        return base


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------


@dataclass
class AnnIndex:
    dim: int
    rows: int
    cells: int
    centroids: np.ndarray       # (cells, dim) float32
    ptr: np.ndarray             # (cells + 1,) int32
    chunk_ids: np.ndarray       # (rows,) int32
    vectors: np.ndarray         # (rows, dim) int8 or a memmap of the same
    scale: np.ndarray           # (rows,) float32
    meta: Dict[str, object] = field(default_factory=dict)

    # -- construction -------------------------------------------------------

    @classmethod
    def build(cls, store, cfg: Optional[AnnConfig] = None,
              verbose: bool = False) -> "AnnIndex":
        """Read every vector out of the store and write an index in memory.

        Two passes over the table. The first assigns each row to a cell and
        writes the quantised row to a temporary array; the second reorders
        those rows so each cell is contiguous, which is what makes a query
        read one slice instead of gathering at random.
        """
        cfg = cfg or AnnConfig()
        total, max_id = store.vector_fingerprint()
        if total == 0:
            raise AnnError("the store has no vectors; run `vrusha harvest` or "
                           "`vrusha ingest` first")

        dim = store.embed_dim()
        if not dim:
            raise AnnError("the store does not record an embedding dimension")

        # --- sample for the centroids -------------------------------------
        sample_rows: List[np.ndarray] = []
        seen = 0
        for ids, matrix in store.iter_vectors(batch=min(4096, max(1, cfg.train_sample))):
            sample_rows.append(matrix)
            seen += matrix.shape[0]
            if seen >= cfg.train_sample:
                break
        sample = _normalise(np.vstack(sample_rows))
        if sample.shape[1] != dim:
            raise AnnError(f"vectors are {sample.shape[1]}-dimensional but the "
                           f"store records dim={dim}")

        target = max(64, int(cfg.target_cell_size))
        cells = max(1, min(65536, int(round(total / target))))
        cells = min(cells, sample.shape[0])
        started = time.time()
        centroids = _kmeans(sample, cells, cfg.iters, cfg.seed)
        if verbose:
            print(f"  centroids   {centroids.shape[0]:,} from "
                  f"{sample.shape[0]:,} sampled rows ({time.time() - started:.1f}s)")

        # --- pass 1: quantise and assign ----------------------------------
        storage = cfg.dtype
        if storage == "auto":
            fits = total * dim * 4 <= cfg.ram_limit_mb * 1024 * 1024
            storage = "float32" if fits else "int8"
        if storage not in ("int8", "float32"):
            raise AnnError(f"ann.dtype is {cfg.dtype!r}; use int8, float32 or auto")
        quantised = np.empty((total, dim), dtype=np.int8 if storage == "int8"
                             else np.float32)
        scales = np.empty(total, dtype=np.float32)
        ids_all = np.empty(total, dtype=np.int32)
        labels = np.empty(total, dtype=np.int32)

        filled = 0
        for ids, matrix in store.iter_vectors(batch=8192):
            block = _normalise(matrix)
            if storage == "int8":
                q, s = _quantise(block)
            else:
                q, s = block, np.ones(block.shape[0], dtype=np.float32)
            take = min(q.shape[0], total - filled)
            quantised[filled:filled + take] = q[:take]
            scales[filled:filled + take] = s[:take]
            ids_all[filled:filled + take] = np.asarray(ids[:take], dtype=np.int32)
            labels[filled:filled + take] = _assign(block[:take], centroids)
            filled += take
            if filled >= total:
                break
        if filled < total:
            quantised = quantised[:filled]
            scales = scales[:filled]
            ids_all = ids_all[:filled]
            labels = labels[:filled]

        # --- pass 2: group rows by cell (CSR) -----------------------------
        order = np.argsort(labels, kind="stable").astype(np.int32)
        counts = np.bincount(labels, minlength=centroids.shape[0])
        ptr = np.zeros(counts.shape[0] + 1, dtype=np.int32)
        np.cumsum(counts, out=ptr[1:])

        index = cls(
            dim=dim,
            rows=int(filled),
            cells=int(centroids.shape[0]),
            centroids=centroids,
            ptr=ptr,
            chunk_ids=ids_all[order],
            vectors=quantised[order],
            scale=scales[order],
            meta={
                "version": VERSION,
                "dim": int(dim),
                "rows": int(filled),
                "cells": int(centroids.shape[0]),
                "dtype": storage,
                "target_cell_size": target,
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "fingerprint": [int(filled), int(max_id)],
                #: O(1) freshness: the store bumps this on every vector write,
                #: so `is_stale` never has to count the table
                "rev": (int(store.vector_revision())
                        if hasattr(store, "vector_revision") else None),
                "build_seconds": round(time.time() - started, 2),
            },
        )
        return index

    # -- persistence --------------------------------------------------------

    def save(self, directory: str) -> Dict[str, object]:
        """Write the seven files. Atomic per file, so a crash leaves no half-index."""
        os.makedirs(directory, exist_ok=True)
        self._write(directory, CENTROIDS, self.centroids, np.float32)
        self._write(directory, PTR, self.ptr, np.int32)
        self._write(directory, ROWS, self.chunk_ids, np.int32)
        self._write(directory, SCALE, self.scale, np.float32)
        self._write(directory, VEC_I8 if self.meta.get("dtype") == "int8" else VEC_F32,
                    self.vectors, np.int8 if self.meta.get("dtype") == "int8" else np.float32)
        # meta last: its presence is what makes the directory a complete index
        path = os.path.join(directory, META)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.meta, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        return self.meta

    @staticmethod
    def _write(directory: str, name: str, array: np.ndarray, dtype) -> None:
        path = os.path.join(directory, name)
        temporary = path + ".tmp"
        with open(temporary, "wb") as handle:
            handle.write(np.ascontiguousarray(array, dtype=dtype).tobytes())
        os.replace(temporary, path)

    @classmethod
    def load(cls, directory: str, ram_limit_mb: int = 512) -> Optional["AnnIndex"]:
        """Read an index back. Returns None when the directory is not one.

        `vec.i8` is the only large file and the only one that can be mmapped.
        Below the RAM limit it is read in full, because a random gather from a
        page cache that keeps evicting is slower than holding 300 MB; above it
        the kernel pages in just the cells a query opens.
        """
        meta_path = os.path.join(directory, META)
        if not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            return None
        if int(meta.get("version", 0)) != VERSION:
            return None

        dim = int(meta["dim"])
        rows = int(meta["rows"])
        cells = int(meta["cells"])

        def read(name: str, dtype, shape) -> Optional[np.ndarray]:
            path = os.path.join(directory, name)
            if not os.path.exists(path):
                return None
            array = np.fromfile(path, dtype=dtype)
            if array.size != int(np.prod(shape)):
                return None
            return array.reshape(shape)

        centroids = read(CENTROIDS, np.float32, (cells, dim))
        ptr = read(PTR, np.int32, (cells + 1,))
        chunk_ids = read(ROWS, np.int32, (rows,))
        scale = read(SCALE, np.float32, (rows,))
        if any(part is None for part in (centroids, ptr, chunk_ids, scale)):
            return None

        dtype = str(meta.get("dtype", "int8"))
        vector_path = os.path.join(directory, VEC_I8 if dtype == "int8" else VEC_F32)
        if not os.path.exists(vector_path):
            return None
        width = 1 if dtype == "int8" else 4
        if rows * dim * width <= int(ram_limit_mb) * 1024 * 1024:
            vectors: np.ndarray = np.fromfile(
                vector_path, dtype=np.int8 if dtype == "int8" else np.float32
            ).reshape(rows, dim)
        else:
            vectors = np.memmap(vector_path, dtype=np.int8 if dtype == "int8" else np.float32,
                                mode="r", shape=(rows, dim))

        return cls(dim=dim, rows=rows, cells=cells, centroids=centroids,
                   ptr=ptr, chunk_ids=chunk_ids,
                   vectors=vectors, scale=scale, meta=meta)

    # -- freshness ----------------------------------------------------------

    def fingerprint(self) -> List[int]:
        return [int(self.rows), int(self.meta.get("fingerprint", [0, 0])[1])]

    def is_stale(self, store) -> bool:
        """True when the store has moved on since this index was built.

        A stale index is worse than no index: it answers confidently from a
        corpus that no longer exists. Callers fall back to the exact scan.

        Uses the store's revision counter, which is O(1). Counting the table
        here cost 1.8 s per query at 200k rows, which made the index pointless.
        Indexes built before the counter existed fall back to the fingerprint.
        """
        rev = self.meta.get("rev")
        if rev is not None:
            return int(store.vector_revision()) != int(rev)
        total, max_id = store.vector_fingerprint()
        return self.fingerprint() != [int(total), int(max_id)]

    # -- search -------------------------------------------------------------

    def nprobe_for(self, cfg: AnnConfig, requested: int = 0) -> int:
        """How many cells to open.

        The default scales with the cell count, so the number of scored rows
        stays roughly constant as the corpus grows — that is the whole point
        of an index. 10% of the cells, at least eight, at most 128.

        Measured (bench/ann_bench.py, 30k x 128): a clustered corpus keeps
        100% recall@10 at two cells; near-duplicates keep 99.9% at one; a
        long-tail corpus needs eight to reach 74%, and an isotropic one needs
        every cell. The default is set for the corpus a RAG store actually
        holds after dedup, and the config is the dial for the rest.
        `nprobe = cells` is exactly the full scan — and still faster than the
        old path, because it never unpacks a blob.
        """
        if requested > 0:
            return min(int(requested), self.cells)
        if cfg.nprobe > 0:
            return min(int(cfg.nprobe), self.cells)
        return min(self.cells, max(8, min(128, int(math.ceil(self.cells * 0.10)))))

    def search(self, query_vector: np.ndarray, k: int, *, store=None,
               cfg: Optional[AnnConfig] = None, nprobe: int = 0) -> List[Tuple[int, float]]:
        """Cosine-ish top k as (chunk_id, score), best first.

        Scores are cosine in [-1, 1] so the caller's floor keeps its meaning.
        Without a store the returned score is the int8 estimate; with one the
        top `rescore` candidates are recomputed from the float32 blob, which is
        what makes the result match the exact scan.
        """
        cfg = cfg or AnnConfig()
        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if query.shape[0] != self.dim:
            raise AnnError(f"query is {query.shape[0]}-dimensional, the index is "
                           f"{self.dim}-dimensional; rebuild the index after "
                           f"changing the embedder")
        if self.rows == 0 or k <= 0:
            return []
        query = _normalise(query.reshape(1, -1))[0]

        # 1. route: which cells could hold the answer
        cell_scores = self.centroids @ query
        probes = self.nprobe_for(cfg, nprobe)
        if probes >= self.cells:
            chosen = np.arange(self.cells)
        else:
            chosen = np.argpartition(-cell_scores, probes - 1)[:probes]

        # 2. gather the rows of those cells: contiguous, because rows are stored in cell order
        slices = [np.arange(int(self.ptr[c]), int(self.ptr[c + 1]), dtype=np.int32)
                  for c in chosen]
        slices = [part for part in slices if part.size]
        if not slices:
            return []
        rows = np.concatenate(slices)
        if rows.size == 0:
            return []

        # 3. score them at int8 width, then widen the shortlist
        if self.meta.get("dtype") == "int8":
            block = np.asarray(self.vectors[rows], dtype=np.float32) * self.scale[rows][:, None]
        else:
            block = np.asarray(self.vectors[rows])
        scores = block @ query
        shortlist = min(rows.size, max(k, int(cfg.rescore)))
        if shortlist < rows.size:
            top = np.argpartition(-scores, shortlist - 1)[:shortlist]
        else:
            top = np.arange(rows.size)

        ids = self.chunk_ids[rows[top]]
        estimated = scores[top]

        if store is not None:
            ids, estimated = self._rescore(store, ids, estimated, query, k)

        order = np.argsort(-estimated)[:k]
        return [(int(ids[i]), float(estimated[i])) for i in order]

    def _rescore(self, store, ids: np.ndarray, estimated: np.ndarray,
                 query: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray]:
        """Read the float32 vectors of the shortlist and score them exactly."""
        try:
            found, matrix = store.get_vectors([int(i) for i in ids])
        except Exception:  # a store that cannot answer leaves the estimate in place
            return ids, estimated
        if not found:
            return ids, estimated
        exact = _normalise(np.asarray(matrix, dtype=np.float32)) @ query
        by_id = {int(chunk_id): float(score) for chunk_id, score in zip(found, exact)}
        scores = np.array([by_id.get(int(i), float(estimated[n]))
                           for n, i in enumerate(ids)], dtype=np.float32)
        return ids, scores

    # -- reporting ----------------------------------------------------------

    def stats(self) -> Dict[str, object]:
        sizes = np.diff(self.ptr)
        return {
            "rows": self.rows,
            "cells": self.cells,
            "dim": self.dim,
            "dtype": str(self.meta.get("dtype", "int8")),
            "rows_per_cell": {
                "min": int(sizes.min()) if sizes.size else 0,
                "median": int(np.median(sizes)) if sizes.size else 0,
                "max": int(sizes.max()) if sizes.size else 0,
            },
            "bytes": int(self.rows * self.dim * (1 if self.meta.get("dtype") == "int8" else 4)
                         + self.rows * 4 * 3 + self.cells * self.dim * 4),
            "mmapped": isinstance(self.vectors, np.memmap),
            "built_at": self.meta.get("built_at"),
            "fingerprint": self.fingerprint(),
        }


# ---------------------------------------------------------------------------
# the store-facing helpers
# ---------------------------------------------------------------------------

_CACHE: Dict[str, Tuple[Tuple[int, int], AnnIndex]] = {}


def index_dir(cfg: Optional[dict]) -> Optional[str]:
    """Where the index lives. `ann.dir` overrides `paths.index`."""
    if not cfg:
        return None
    block = cfg.get("ann", {}) or {}
    named = block.get("dir")
    try:
        from ..config import resolve
        if named:
            if os.path.isabs(named):
                return named
            return os.path.abspath(os.path.join(resolve(cfg, "root"), named))
        return resolve(cfg, "index")
    except Exception:
        return os.path.abspath(named) if named else None


def mode(cfg: Optional[dict]) -> str:
    return str(((cfg or {}).get("ann", {}) or {}).get("mode", "auto")).lower()


def load_for(store, cfg: Optional[dict]) -> Optional[AnnIndex]:
    """The index for this store, or None if there isn't a usable one.

    Cached per (directory, fingerprint) so a query does not stat six files.
    Never raises: any problem means "fall back to the exact scan", because a
    retrieval path that can fail is worse than a slow one.
    """
    if mode(cfg) == "off":
        return None
    directory = index_dir(cfg)
    if not directory:
        return None
    try:
        ann_cfg = AnnConfig.from_cfg(cfg)
        cached = _CACHE.get(directory)
        if cached is not None:
            fingerprint, index = cached
            if not index.is_stale(store):
                return index
            _CACHE.pop(directory, None)
        index = AnnIndex.load(directory, ram_limit_mb=ann_cfg.ram_limit_mb)
        if index is None:
            return None
        if index.is_stale(store):
            return None
        if mode(cfg) == "auto":
            minimum = int((cfg.get("ann", {}) or {}).get("min_vectors", 20_000))
            if index.rows < minimum:
                return None
        _CACHE[directory] = (tuple(index.fingerprint()), index)
        return index
    except Exception:
        return None


def build_for(store, cfg: Optional[dict], verbose: bool = False) -> AnnIndex:
    """Build, save, and cache. The one path `vrusha index build` uses."""
    directory = index_dir(cfg)
    if not directory:
        raise AnnError("no ann.dir in the config; set one or run `vrusha doctor`")
    index = AnnIndex.build(store, AnnConfig.from_cfg(cfg), verbose=verbose)
    index.save(directory)
    _CACHE[directory] = (tuple(index.fingerprint()), index)
    return index


def drop_for(cfg: Optional[dict]) -> bool:
    """Remove the index directory. The store is untouched, by design."""
    directory = index_dir(cfg)
    if not directory or not os.path.isdir(directory):
        return False
    for name in (META, CENTROIDS, PTR, ROWS, VEC_I8, VEC_F32, SCALE):
        path = os.path.join(directory, name)
        if os.path.exists(path):
            os.remove(path)
    _CACHE.pop(directory, None)
    try:
        os.rmdir(directory)
    except OSError:
        pass
    return True
