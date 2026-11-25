"""The store: one SQLite file holding documents, chunks, vectors, edges and the
address of every unit.

Why SQLite: it is a single file you can copy, back up and query with any tool.
No server, no ports, no daemon to forget about. FTS5 gives BM25 for free and a
vector is just a BLOB, so the two kinds of search live in the same transaction.

Tables
------
    documents    one row per fetched page or file
    chunks       one row per unit of meaning — this is the addressable thing
    vectors      chunk_id -> float32 BLOB (or int8 when you set dtype)
    edges        chunk -> chunk links: merged_into, near, child_of
    address_log  every address change, so a reflow is auditable
    centroids    the cluster centres from the analyse pass
    sig_bands    LSH bands for the near-duplicate gate
    passes       what each pass did, and when
    chunks_fts   FTS5 external-content index over chunks.text

Nothing here embeds, clusters or decides. It stores, and it answers questions
about what it stores. The passes do the thinking.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .address import Address
from .embed import pack, unpack

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS documents (
  doc_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  url          TEXT NOT NULL,
  url_hash     TEXT UNIQUE NOT NULL,
  domain       TEXT,
  title        TEXT,
  kind         TEXT,
  lang         TEXT,
  licence      TEXT,
  retrieved    TEXT NOT NULL,
  published    TEXT,
  text         TEXT NOT NULL,
  chars        INTEGER,
  content_hash TEXT,
  status       INTEGER,
  meta         TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id       INTEGER NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ordinal      INTEGER,
  kind         TEXT NOT NULL,
  symbol       TEXT,
  lang         TEXT,
  text         TEXT NOT NULL,
  chars        INTEGER,
  start_line   INTEGER,
  end_line     INTEGER,
  sha          TEXT,
  address      TEXT,
  addr_domain  INTEGER,
  addr_family  INTEGER,
  addr_kind    INTEGER,
  addr_cluster INTEGER,
  addr_ordinal INTEGER,
  active       INTEGER DEFAULT 1,
  merged_into  INTEGER,
  quality      REAL DEFAULT 1.0,
  sig          TEXT,
  created      TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_address ON chunks(address);
CREATE INDEX IF NOT EXISTS idx_chunks_prefix  ON chunks(addr_domain, addr_family, addr_kind, addr_cluster);
CREATE INDEX IF NOT EXISTS idx_chunks_doc     ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_active  ON chunks(active);

CREATE TABLE IF NOT EXISTS vectors (
  chunk_id INTEGER PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  dim      INTEGER NOT NULL,
  dtype    TEXT NOT NULL DEFAULT 'float32',
  blob     BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
  src    INTEGER NOT NULL,
  dst    INTEGER NOT NULL,
  kind   TEXT NOT NULL,
  weight REAL DEFAULT 1.0,
  PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, kind);

CREATE TABLE IF NOT EXISTS address_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  chunk_id     INTEGER NOT NULL,
  old_address  TEXT,
  new_address  TEXT,
  pass_id      TEXT,
  ts           TEXT
);

CREATE TABLE IF NOT EXISTS centroids (
  domain  INTEGER NOT NULL,
  family  INTEGER NOT NULL,
  kind    INTEGER NOT NULL,
  cluster INTEGER NOT NULL,
  dim     INTEGER NOT NULL,
  blob    BLOB NOT NULL,
  size    INTEGER,
  PRIMARY KEY (domain, family, kind, cluster)
);

CREATE TABLE IF NOT EXISTS sig_bands (
  band_key TEXT NOT NULL,
  chunk_id INTEGER NOT NULL,
  PRIMARY KEY (band_key, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_sig_bands_key ON sig_bands(band_key);

CREATE TABLE IF NOT EXISTS passes (
  run_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  pass_id  TEXT NOT NULL,
  ts       TEXT NOT NULL,
  stats    TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, symbol,
  content='chunks', content_rowid='chunk_id',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text, symbol) VALUES (new.chunk_id, new.text, coalesce(new.symbol,''));
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, symbol) VALUES('delete', old.chunk_id, old.text, coalesce(old.symbol,''));
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF text, symbol ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, symbol) VALUES('delete', old.chunk_id, old.text, coalesce(old.symbol,''));
  INSERT INTO chunks_fts(rowid, text, symbol) VALUES (new.chunk_id, new.text, coalesce(new.symbol,''));
END;
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DimensionError(ValueError):
    """A vector does not match the width this store was built with.

    Mixing widths does not corrupt anything -- every vector keeps its own
    dimension in its row -- but retrieval compares shapes and silently drops
    the whole vector signal. Failing loudly at write time is the only way that
    stays visible.
    """


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:32]


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


class StoreDuplicateIndex:
    """The interface `gate_duplicate` wants: band lookup plus signatures."""

    def __init__(self, store: "Store"):
        self.store = store

    def lookup(self, band_keys: Sequence[str]) -> List[int]:
        if not band_keys:
            return []
        placeholders = ",".join("?" for _ in band_keys)
        rows = self.store.conn.execute(
            f"SELECT DISTINCT chunk_id FROM sig_bands WHERE band_key IN ({placeholders})",
            list(band_keys),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def signature_of(self, chunk_id: int) -> List[int]:
        row = self.store.conn.execute(
            "SELECT sig FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        if not row or not row[0]:
            return []
        from ..harvest.gates import signature_from_hex
        return signature_from_hex(row[0])


class Store:
    """Open (or create) the database and answer questions about it."""

    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._check_version()
        self.conn.commit()

    # ------------------------------------------------------------------
    def _check_version(self) -> None:
        row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),))
        elif int(row[0]) != SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema is version {row[0]}, this code expects {SCHEMA_VERSION}. "
                "Move the file aside and re-ingest."
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    def add_document(
        self,
        url: str,
        title: str,
        text: str,
        *,
        domain: str = "",
        kind: str = "prose",
        lang: str = "en",
        licence: Optional[str] = None,
        retrieved: str = "",
        published: Optional[str] = None,
        status: int = 200,
        content_hash: str = "",
        meta: Optional[dict] = None,
    ) -> Optional[int]:
        """Insert, or return the existing doc_id. A page is stored once."""
        key = url_hash(url)
        existing = self.conn.execute(
            "SELECT doc_id FROM documents WHERE url_hash = ?", (key,)).fetchone()
        if existing:
            return int(existing[0])
        cursor = self.conn.execute(
            """INSERT INTO documents
               (url, url_hash, domain, title, kind, lang, licence, retrieved, published,
                text, chars, content_hash, status, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (url, key, domain, title, kind, lang, licence, retrieved or now_utc(), published,
             text, len(text), content_hash or text_sha(text), status,
             json.dumps(meta or {}, ensure_ascii=False)),
        )
        return int(cursor.lastrowid)

    def document_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def search_documents(self, query: str, limit: int = 10) -> List[dict]:
        """Title and body search over documents — used by the `store` backend."""
        terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if len(t) > 2]
        if not terms:
            return []
        where = " AND ".join("(title LIKE ? OR text LIKE ?)" for _ in terms)
        params: List[str] = []
        for term in terms:
            params.extend([f"%{term}%", f"%{term}%"])
        rows = self.conn.execute(
            f"SELECT url, title, domain, text FROM documents WHERE {where} LIMIT ?",
            (*params, limit)).fetchall()
        return [
            {"url": r["url"], "title": r["title"], "domain": r["domain"],
             "snippet": (r["text"] or "")[:400], "score": 1.0}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # chunks
    # ------------------------------------------------------------------
    def add_chunk(
        self,
        doc_id: int,
        text: str,
        *,
        kind: str,
        symbol: str = "",
        lang: str = "",
        ordinal: int = 0,
        start_line: int = 0,
        end_line: int = 0,
        quality: float = 1.0,
        sig: str = "",
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO chunks
               (doc_id, ordinal, kind, symbol, lang, text, chars, start_line, end_line,
                sha, quality, sig, created)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, ordinal, kind, symbol, lang, text, len(text), start_line, end_line,
             text_sha(text), quality, sig, now_utc()),
        )
        return int(cursor.lastrowid)

    def embed_dim(self) -> Optional[int]:
        """The width this store was built with, or None before the first vector."""
        row = self.conn.execute("SELECT value FROM meta WHERE key='embed_dim'").fetchone()
        return int(row[0]) if row else None

    def set_vector(self, chunk_id: int, vector: np.ndarray, dtype: str = "float32") -> None:
        """Store one vector, refusing a width that does not match the store."""
        array = np.asarray(vector, dtype=np.float32).ravel()
        if array.size == 0:
            raise DimensionError(f"chunk {chunk_id}: empty vector")
        if not np.all(np.isfinite(array)):
            raise DimensionError(f"chunk {chunk_id}: vector contains NaN or infinity")
        expected = self.embed_dim()
        if expected is None:
            self.conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('embed_dim', ?)",
                (str(int(array.size)),))
        elif int(array.size) != expected:
            raise DimensionError(
                f"chunk {chunk_id}: vector has {array.size} dimensions but this store "
                f"was built with {expected}. Re-embed the whole store with one model "
                f"(`vrusha ingest` after `store.clear_vectors()`), or point the config at "
                f"the embedder that produced the stored vectors.")
        blob, dtype_name, dim = pack(array, dtype)
        self.conn.execute(
            "INSERT OR REPLACE INTO vectors(chunk_id, dim, dtype, blob) VALUES (?,?,?,?)",
            (chunk_id, dim, dtype_name, blob))

    def clear_vectors(self) -> int:
        """Drop every vector and the recorded width. The documented re-embed path."""
        removed = int(self.conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])
        self.conn.execute("DELETE FROM vectors")
        self.conn.execute("DELETE FROM meta WHERE key='embed_dim'")
        return removed

    def get_vectors(self, chunk_ids: Optional[Sequence[int]] = None) -> Tuple[List[int], np.ndarray]:
        if chunk_ids is None:
            rows = self.conn.execute(
                "SELECT chunk_id, dim, dtype, blob FROM vectors ORDER BY chunk_id").fetchall()
        else:
            if not chunk_ids:
                return [], np.zeros((0, 0), dtype=np.float32)
            placeholders = ",".join("?" for _ in chunk_ids)
            rows = self.conn.execute(
                f"SELECT chunk_id, dim, dtype, blob FROM vectors WHERE chunk_id IN ({placeholders}) "
                "ORDER BY chunk_id", list(chunk_ids)).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        dim = int(rows[0]["dim"])
        matrix = np.zeros((len(rows), dim), dtype=np.float32)
        ids: List[int] = []
        for index, row in enumerate(rows):
            matrix[index] = unpack(row["blob"], row["dtype"], int(row["dim"]))
            ids.append(int(row["chunk_id"]))
        return ids, matrix

    def vector_dim(self) -> Optional[int]:
        row = self.conn.execute("SELECT dim FROM vectors LIMIT 1").fetchone()
        return int(row[0]) if row else None

    def all_chunks(self, active_only: bool = True) -> List[sqlite3.Row]:
        query = "SELECT * FROM chunks"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY chunk_id"
        return self.conn.execute(query).fetchall()

    def chunk(self, chunk_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()

    def chunk_count(self, active_only: bool = True) -> int:
        query = "SELECT COUNT(*) FROM chunks" + (" WHERE active = 1" if active_only else "")
        return int(self.conn.execute(query).fetchone()[0])

    def set_address(self, chunk_id: int, address: Address, pass_id: str = "assign.address") -> None:
        row = self.conn.execute("SELECT address FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        old = row[0] if row else None
        new = str(address)
        if old == new:
            return
        self.conn.execute(
            """UPDATE chunks SET address=?, addr_domain=?, addr_family=?, addr_kind=?,
               addr_cluster=?, addr_ordinal=? WHERE chunk_id=?""",
            (new, *address.segments, chunk_id))
        self.conn.execute(
            "INSERT INTO address_log(chunk_id, old_address, new_address, pass_id, ts) "
            "VALUES (?,?,?,?,?)", (chunk_id, old, new, pass_id, now_utc()))

    def address_of(self, chunk_id: int) -> Optional[Address]:
        row = self.conn.execute("SELECT address FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        if not row or not row[0]:
            return None
        return Address.parse(row[0])

    def addresses(self, active_only: bool = True) -> List[Tuple[int, Address]]:
        query = "SELECT chunk_id, address FROM chunks WHERE address IS NOT NULL"
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY chunk_id"
        return [(int(r[0]), Address.parse(r[1])) for r in self.conn.execute(query).fetchall()]

    def chunks_in_region(self, prefix: Address, limit: int = 50) -> List[sqlite3.Row]:
        depth = prefix.depth
        if depth == 0:
            return self.all_chunks()[:limit]
        columns = ["addr_domain", "addr_family", "addr_kind", "addr_cluster", "addr_ordinal"][:depth]
        where = " AND ".join(f"{c} = ?" for c in columns)
        return self.conn.execute(
            f"SELECT * FROM chunks WHERE {where} AND active = 1 "
            "ORDER BY addr_cluster, addr_ordinal LIMIT ?",
            (*prefix.segments[:depth], limit)).fetchall()

    # ------------------------------------------------------------------
    # edges
    # ------------------------------------------------------------------
    def add_edge(self, src: int, dst: int, kind: str, weight: float = 1.0) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO edges(src, dst, kind, weight) VALUES (?,?,?,?)",
            (src, dst, kind, weight))

    def neighbours(self, chunk_id: int, kind: Optional[str] = None, limit: int = 10) -> List[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                "SELECT * FROM edges WHERE src=? AND kind=? ORDER BY weight DESC LIMIT ?",
                (chunk_id, kind, limit)).fetchall()
        return self.conn.execute(
            "SELECT * FROM edges WHERE src=? ORDER BY weight DESC LIMIT ?",
            (chunk_id, limit)).fetchall()

    def edge_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])

    # ------------------------------------------------------------------
    # signatures / duplicates
    # ------------------------------------------------------------------
    def duplicate_index(self) -> StoreDuplicateIndex:
        return StoreDuplicateIndex(self)

    def set_signature(self, chunk_id: int, sig_hex: str, band_keys: Sequence[str]) -> None:
        self.conn.execute("UPDATE chunks SET sig=? WHERE chunk_id=?", (sig_hex, chunk_id))
        self.conn.executemany(
            "INSERT OR IGNORE INTO sig_bands(band_key, chunk_id) VALUES (?,?)",
            [(key, chunk_id) for key in band_keys])

    def deactivate(self, chunk_id: int, merged_into: int, weight: float = 1.0) -> None:
        self.conn.execute("UPDATE chunks SET active=0, merged_into=? WHERE chunk_id=?",
                          (merged_into, chunk_id))
        self.add_edge(chunk_id, merged_into, "merged_into", weight)

    # ------------------------------------------------------------------
    # centroids
    # ------------------------------------------------------------------
    def set_centroid(self, domain: int, family: int, kind: int, cluster: int,
                     vector: np.ndarray, size: int) -> None:
        blob, _dtype, dim = pack(vector)
        self.conn.execute(
            "INSERT OR REPLACE INTO centroids(domain, family, kind, cluster, dim, blob, size) "
            "VALUES (?,?,?,?,?,?,?)",
            (domain, family, kind, cluster, dim, blob, size))

    def get_centroids(self) -> List[dict]:
        rows = self.conn.execute("SELECT * FROM centroids ORDER BY domain, family, kind, cluster").fetchall()
        return [
            {"domain": r["domain"], "family": r["family"], "kind": r["kind"],
             "cluster": r["cluster"], "dim": r["dim"], "size": r["size"],
             "vector": unpack(r["blob"], "float32", int(r["dim"]))}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # passes log
    # ------------------------------------------------------------------
    def log_pass(self, pass_id: str, stats: dict) -> None:
        self.conn.execute(
            "INSERT INTO passes(pass_id, ts, stats) VALUES (?,?,?)",
            (pass_id, now_utc(), json.dumps(stats, ensure_ascii=False, default=str)))

    def pass_history(self, limit: int = 20) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM passes ORDER BY run_id DESC LIMIT ?", (limit,)).fetchall()
        return [
            {"pass_id": r["pass_id"], "ts": r["ts"], "stats": json.loads(r["stats"] or "{}")}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def fts_search(self, query: str, limit: int = 60) -> List[Tuple[int, float]]:
        """BM25 over chunks. Returns (chunk_id, score) with higher = better."""
        match = self._fts_query(query)
        if not match:
            return []
        try:
            rows = self.conn.execute(
                """SELECT c.chunk_id AS chunk_id, bm25(chunks_fts) AS score
                   FROM chunks_fts
                   JOIN chunks c ON c.chunk_id = chunks_fts.rowid
                   WHERE chunks_fts MATCH ? AND c.active = 1
                   ORDER BY score LIMIT ?""",
                (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(int(r["chunk_id"]), -float(r["score"])) for r in rows]

    @staticmethod
    def _fts_query(query: str) -> str:
        """Quote every token. An unquoted ':' or '*' is a syntax error in FTS5."""
        tokens = [t for t in "".join(c if (c.isalnum() or c == "_") else " " for c in query).split()
                  if len(t) > 1]
        if not tokens:
            return ""
        return " OR ".join(f'"{t}"' for t in tokens[:32])

    # ------------------------------------------------------------------
    # housekeeping
    # ------------------------------------------------------------------
    def commit(self) -> None:
        self.conn.commit()

    @contextmanager
    def transaction(self):
        """All-or-nothing around a group of writes.

        A SAVEPOINT nests inside the connection's implicit transaction, so a
        failure rolls back only what this block wrote and leaves the rest of
        the run intact. Used by ingestion so a document cannot survive as
        rows without chunks or chunks without vectors.
        """
        name = "vrusha_tx"
        self.conn.execute(f"SAVEPOINT {name}")
        try:
            yield self
        except BaseException:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.conn.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            self.conn.execute(f"RELEASE SAVEPOINT {name}")

    def stats(self) -> dict:
        docs = self.document_count()
        chunks = self.chunk_count()
        total = self.chunk_count(active_only=False)
        addressed = int(self.conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE address IS NOT NULL AND active=1").fetchone()[0])
        domains = self.conn.execute(
            "SELECT addr_domain, COUNT(*) FROM chunks WHERE address IS NOT NULL AND active=1 "
            "GROUP BY addr_domain ORDER BY addr_domain").fetchall()
        langs = self.conn.execute(
            "SELECT lang, COUNT(*) FROM chunks WHERE active=1 GROUP BY lang ORDER BY COUNT(*) DESC"
        ).fetchall()
        licences = self.conn.execute(
            "SELECT licence, COUNT(*) FROM documents GROUP BY licence ORDER BY COUNT(*) DESC").fetchall()
        return {
            "documents": docs,
            "chunks": chunks,
            "chunks_total": total,
            "addressed": addressed,
            "edges": self.edge_count(),
            "address_log": int(self.conn.execute("SELECT COUNT(*) FROM address_log").fetchone()[0]),
            "domains": {str(r[0]): int(r[1]) for r in domains},
            "languages": {(r[0] or "?"): int(r[1]) for r in langs},
            "licences": {(r[0] or "unknown"): int(r[1]) for r in licences},
            "vector_dim": self.vector_dim(),
            "embed_dim": self.embed_dim(),
            "path": os.path.abspath(self.path),
        }
