"""sqlite-vec: vector search inside SQLite, as a third backend.

`sqlite-vec` is a single C file that adds a `vec0` virtual table to SQLite —
float, int8 and binary vectors, KNN with a `MATCH` constraint, no dependencies,
MIT/Apache-2.0. The reason it belongs here rather than in a survey appendix: this
store is *already* SQLite, so the third vector engine needs no new format, no
server and no index files to keep in step.

What it is not: an ANN index. `sqlite-vec` is brute-force, in C, with
quantisation — fast enough for the sizes this project targets and honest about
the ones it does not. That is why it joins the router as a *third* option
instead of replacing the native IVF: above a million vectors the IVF wins on
work done, and below it the C loop wins on constant factor.

Layout: a separate `<store>.vec0.db` file beside the store database. The store
is the truth; this is a cache like the ANN index, and deleting the file loses
nothing.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Optional

import numpy as np

from .backends import Backend, BackendStats, Hit


def _import():
    try:
        import sqlite_vec                    # noqa: F401  (optional dependency)
        return sqlite_vec
    except Exception:                        # noqa: BLE001 - absent is a state
        return None


class Vec0Backend(Backend):
    """A `vec0` virtual table in its own database file, rebuilt from the store."""

    name = "sqlite-vec"
    capabilities = frozenset({"vector", "quantised"})

    def __init__(self, store, cfg: dict, *, path: Optional[str] = None):
        self.store = store
        self.cfg = cfg or {}
        self.module = _import()
        self.path = path or self._default_path()
        # opening the file and loading the extension costs ~25 ms: far more than
        # the search itself at small sizes, so the connection is kept
        self._connection: Optional[sqlite3.Connection] = None

    def _default_path(self) -> str:
        base = getattr(self.store, "path", "") or ""
        return (base + ".vec0.db") if base else ":memory:"

    # -- lifecycle --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.enable_load_extension(True)
            self.module.load(connection)
            connection.enable_load_extension(False)
            self._connection = connection
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def available(self) -> bool:
        if self.module is None:
            return False
        if self.path == ":memory:":
            return True
        return os.path.exists(self.path)

    def build(self) -> Dict[str, object]:
        """Write every vector in the store into a fresh `vec0` table."""
        if self.module is None:
            raise RuntimeError("sqlite-vec is not installed: pip install sqlite-vec")

        ids, matrix = self.store.get_vectors()
        if not ids:
            raise RuntimeError("the store has no vectors")
        matrix = np.asarray(matrix, dtype=np.float32)
        dim = int(matrix.shape[1])

        self.close()
        if os.path.exists(self.path) and self.path != ":memory:":
            os.unlink(self.path)
        connection = self._connect()
        try:
            connection.execute(
                f"CREATE VIRTUAL TABLE vec_chunks USING vec0("
                f"chunk_id integer primary key, "
                f"embedding float[{dim}] distance_metric=cosine)")
            # sqlite-vec wants a JSON array per row; batching keeps it quick
            connection.executemany(
                "INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
                ((int(chunk_id), _json_vector(row))
                 for chunk_id, row in zip(ids, matrix)))
            connection.commit()
        finally:
            pass
        return {"rows": len(ids), "dim": dim, "path": self.path,
                "mb": round(os.path.getsize(self.path) / 1e6, 1)
                if self.path != ":memory:" else 0.0}

    # -- querying ---------------------------------------------------------

    def search(self, vector: np.ndarray, k: int, *, text: str = "",
               filter: Optional[dict] = None, nprobe: int = 0) -> List[Hit]:
        if not self.available():
            raise RuntimeError("no vec0 index: `vrusha index build --engine sqlite-vec`")
        if filter:
            # The router never sends a filtered query here -- `filter` is a
            # capability, and this backend does not declare it. A direct caller
            # might, and silently returning unfiltered rows would be a wrong
            # answer that looks like a right one.
            raise RuntimeError(
                "sqlite-vec cannot apply a filter; the router sends filtered "
                "queries to a backend that can. Use `exact` or `ragforge`.")
        query = np.asarray(vector, dtype=np.float32).ravel()
        rows = self._connect().execute(
            "SELECT chunk_id, distance FROM vec_chunks "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (_json_vector(query), int(k))).fetchall()
        # cosine distance is 1 - cosine similarity
        return [(int(chunk_id), float(1.0 - distance))
                for chunk_id, distance in rows]

    def stats(self) -> BackendStats:
        if self.module is None:
            return BackendStats(self.name, False,
                                detail={"reason": "sqlite-vec is not installed "
                                                  "(pip install sqlite-vec)"})
        if not self.available():
            return BackendStats(self.name, False,
                                detail={"reason": "no vec0 index built",
                                        "path": self.path})
        connection = self._connect()
        rows = connection.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        version = connection.execute("SELECT vec_version()").fetchone()[0]
        size = os.path.getsize(self.path) if self.path != ":memory:" else 0
        return BackendStats(self.name, True, rows=int(rows),
                            bytes=size, detail={"version": version,
                                                "path": self.path})


def _json_vector(values) -> str:
    """sqlite-vec takes a JSON array; numpy floats need to become plain ones."""
    return "[" + ",".join(f"{float(value):.6f}" for value in values) + "]"
