"""Where extracted rows live: three tables beside the chunks, not instead of them.

    x_tables    one row per table found: which document, which chunk, how many
                rows, and why it was judged data rather than layout
    x_columns   the schema: name, type, unit, null rate, samples
    x_rows      one row per record, as JSON

Rows are JSON rather than a column per field on purpose. A corpus meets a new
table shape every day; `ALTER TABLE` per page is not a schema, it is a mess. The
schema lives in `x_columns` where a query can read it, and SQLite's JSON
functions make the rows queryable:

    SELECT json_extract(data,'$.price') FROM x_rows WHERE table_id = 3;

Nothing here replaces the chunk store. A row and the chunk it came from are
joined, so retrieval can answer "what does it say" and SQL can answer "how
many", from one corpus, with one provenance chain.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, Iterable, List, Optional, Sequence

from .extract import (Record, Table, Verdict, judge, schema_of, table_from_csv,
                      tables_from_html, typed_rows)

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_tables (
  table_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id     INTEGER NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  chunk_id   INTEGER REFERENCES chunks(chunk_id) ON DELETE SET NULL,
  name       TEXT NOT NULL,
  source     TEXT,
  n_rows     INTEGER NOT NULL DEFAULT 0,
  n_cols     INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0,
  verdict    TEXT,
  extracted  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_x_tables_doc   ON x_tables(doc_id);
CREATE INDEX IF NOT EXISTS idx_x_tables_chunk ON x_tables(chunk_id);

CREATE TABLE IF NOT EXISTS x_columns (
  table_id  INTEGER NOT NULL REFERENCES x_tables(table_id) ON DELETE CASCADE,
  position  INTEGER NOT NULL,
  name      TEXT NOT NULL,
  type      TEXT NOT NULL,
  unit      TEXT,
  currency  TEXT,
  null_rate REAL NOT NULL DEFAULT 0,
  samples   TEXT,
  PRIMARY KEY (table_id, position)
);

CREATE TABLE IF NOT EXISTS x_rows (
  table_id   INTEGER NOT NULL REFERENCES x_tables(table_id) ON DELETE CASCADE,
  row_number INTEGER NOT NULL,
  data       TEXT NOT NULL,
  PRIMARY KEY (table_id, row_number)
);
CREATE INDEX IF NOT EXISTS idx_x_rows_table ON x_rows(table_id);
"""


def ensure_schema(store) -> None:
    store.conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# storing one table
# ---------------------------------------------------------------------------


def _match_chunk(store, doc_id: int, table: Table) -> Optional[int]:
    """The chunk whose text holds this table, if one does.

    Not every table survives chunking — a table split across two chunks matches
    neither — so a miss is normal and the row keeps its document and span
    instead. A wrong match is worse than none: it would point a price at the
    paragraph next to it.
    """
    probe = max((cell for row in table.rows[:2] for cell in row),
                key=len, default="")
    if len(probe) < 12:
        return None
    row = store.conn.execute(
        "SELECT chunk_id FROM chunks WHERE doc_id = ? AND active = 1 "
        "AND instr(text, ?) > 0 ORDER BY chunk_id LIMIT 1",
        (doc_id, probe[:200])).fetchone()
    return int(row[0]) if row else None


def store_table(store, table: Table, doc_id: int, *, name: str = "",
                dayfirst: bool = True) -> Optional[Dict[str, object]]:
    """Judge, type and insert one table. Returns a summary or None if rejected."""
    ensure_schema(store)
    verdict: Verdict = judge(table)
    if not verdict.relational:
        return None

    columns = schema_of(table, dayfirst=dayfirst)
    records: List[Record] = typed_rows(table, columns, dayfirst=dayfirst)
    if not records:
        return None

    typed = sum(1 for column in columns if column.type != "text")
    confidence = typed / max(1, len(columns))
    chunk_id = _match_chunk(store, doc_id, table)
    label = name or table.caption or f"table_{int(time.time())}"

    cursor = store.conn.execute(
        """INSERT INTO x_tables
           (doc_id, chunk_id, name, source, n_rows, n_cols, confidence, verdict, extracted)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (doc_id, chunk_id, label[:120], table.source, len(records), len(columns),
         round(confidence, 3), verdict.reason,
         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    table_id = int(cursor.lastrowid)

    store.conn.executemany(
        """INSERT INTO x_columns
           (table_id, position, name, type, unit, currency, null_rate, samples)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(table_id, column.position, column.name, column.type, column.unit,
          column.currency, round(column.null_rate, 3),
          json.dumps(column.samples, ensure_ascii=False)) for column in columns])

    store.conn.executemany(
        "INSERT INTO x_rows (table_id, row_number, data) VALUES (?,?,?)",
        [(table_id, record.row_number,
          json.dumps(record.values, ensure_ascii=False, default=str))
         for record in records])

    return {"table_id": table_id, "name": label, "rows": len(records),
            "columns": [column.to_dict() for column in columns],
            "chunk_id": chunk_id, "confidence": round(confidence, 3),
            "verdict": verdict.reason}


# ---------------------------------------------------------------------------
# extracting a file or a directory
# ---------------------------------------------------------------------------


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def extract_path(store, path: str, *, licence: Optional[str] = None,
                 dayfirst: bool = True, verbose: bool = True) -> Dict[str, object]:
    """One file: HTML tables or a CSV, stored against a document."""
    from ..harvest.fetch import html_to_text

    suffix = os.path.splitext(path)[1].lower()
    raw = _read(path)
    tables: List[Table] = []
    if suffix in (".html", ".htm", ".xhtml"):
        tables = tables_from_html(raw, source=path)
        _title, text, _links = html_to_text(raw)
    elif suffix in (".csv", ".tsv"):
        table = table_from_csv(raw, source=path)
        tables = [table] if table else []
        text = raw[:20000]
    else:
        return {"path": path, "tables": 0, "rows": 0, "skipped": "not html or csv"}

    doc_id = store.add_document(url=os.path.abspath(path),
                                title=os.path.basename(path), text=text,
                                licence=licence)
    if doc_id is None:
        return {"path": path, "tables": 0, "rows": 0, "skipped": "document not stored"}

    stored: List[Dict[str, object]] = []
    rejected = 0
    for number, table in enumerate(tables, start=1):
        summary = store_table(store, table, doc_id, dayfirst=dayfirst,
                              name=f"{os.path.basename(path)}#{number}")
        if summary is None:
            rejected += 1
        else:
            stored.append(summary)
    store.commit()

    if verbose:
        rows = sum(int(item["rows"]) for item in stored)
        print(f"  {os.path.basename(path)}: {len(stored)} table(s), {rows} row(s), "
              f"{rejected} rejected")
    return {"path": path, "doc_id": doc_id, "tables": len(stored),
            "rows": sum(int(item["rows"]) for item in stored),
            "rejected": rejected, "stored": stored}


def extract_dir(store, directory: str, *, licence: Optional[str] = None,
                dayfirst: bool = True, limit: int = 0,
                verbose: bool = True) -> Dict[str, object]:
    """Every html and csv under a directory, one document each."""
    files: List[str] = []
    for root, _dirs, names in os.walk(directory):
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() in (".html", ".htm", ".xhtml", ".csv", ".tsv"):
                files.append(os.path.join(root, name))
    if limit:
        files = files[:limit]

    results = [extract_path(store, path, licence=licence, dayfirst=dayfirst,
                            verbose=verbose) for path in files]
    return {
        "files": len(results),
        "tables": sum(int(r["tables"]) for r in results),
        "rows": sum(int(r["rows"]) for r in results),
        "rejected": sum(int(r.get("rejected", 0)) for r in results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# reading it back
# ---------------------------------------------------------------------------


def list_tables(store) -> List[Dict[str, object]]:
    ensure_schema(store)
    rows = store.conn.execute(
        """SELECT t.table_id, t.name, t.n_rows, t.n_cols, t.confidence, t.verdict,
                  t.chunk_id, d.url, d.licence
           FROM x_tables t JOIN documents d ON d.doc_id = t.doc_id
           ORDER BY t.table_id""").fetchall()
    output = []
    for row in rows:
        columns = store.conn.execute(
            "SELECT name, type, unit FROM x_columns WHERE table_id = ? ORDER BY position",
            (int(row["table_id"]),)).fetchall()
        output.append({
            "table_id": int(row["table_id"]), "name": row["name"],
            "rows": int(row["n_rows"]), "cols": int(row["n_cols"]),
            "confidence": float(row["confidence"]), "verdict": row["verdict"],
            "chunk_id": row["chunk_id"], "url": row["url"], "licence": row["licence"],
            "columns": [{"name": c["name"], "type": c["type"], "unit": c["unit"]}
                        for c in columns],
        })
    return output


def table_rows(store, table_id: int, limit: int = 50) -> List[dict]:
    ensure_schema(store)
    rows = store.conn.execute(
        "SELECT row_number, data FROM x_rows WHERE table_id = ? "
        "ORDER BY row_number LIMIT ?", (int(table_id), int(limit))).fetchall()
    return [{"row": int(r["row_number"]), **json.loads(r["data"])} for r in rows]
