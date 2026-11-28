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
from typing import Dict, List, Optional

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

CREATE TABLE IF NOT EXISTS x_equations (
  equation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id      INTEGER NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  chunk_id    INTEGER REFERENCES chunks(chunk_id) ON DELETE SET NULL,
  latex       TEXT NOT NULL,
  canonical   TEXT NOT NULL,
  plain       TEXT,
  symbols     TEXT,
  display     INTEGER NOT NULL DEFAULT 0,
  source      TEXT
);
CREATE INDEX IF NOT EXISTS idx_x_equations_doc ON x_equations(doc_id);
CREATE INDEX IF NOT EXISTS idx_x_equations_canonical ON x_equations(canonical);
"""


def ensure_schema(store) -> None:
    store.conn.executescript(SCHEMA)
    # `kind` arrived after the first release of these tables: add it in place
    # rather than making anyone drop a database to get a column.
    columns = {row[1] for row in
               store.conn.execute("PRAGMA table_info(x_tables)").fetchall()}
    if "kind" not in columns:
        try:
            store.conn.execute("ALTER TABLE x_tables ADD COLUMN kind TEXT")
        except Exception:                       # noqa: BLE001 - already added
            pass
    # `origin` records how a column was produced: a page, a formula, a script
    column_rows = {row[1] for row in
                   store.conn.execute("PRAGMA table_info(x_columns)").fetchall()}
    if "origin" not in column_rows:
        try:
            store.conn.execute("ALTER TABLE x_columns ADD COLUMN origin TEXT")
        except Exception:                       # noqa: BLE001 - already added
            pass


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


def store_entity_table(store, table, doc_id: int) -> Optional[Dict[str, object]]:
    """One schema.org type from a page, as a table with typed columns.

    The page told us what the thing is, so there is no header detection and no
    junk filter: an entity of type `Product` is a row of `Product`, and the
    only judgement left is the type of each column.
    """
    from .jsonld import coerce, column_type

    ensure_schema(store)
    if not table.entities:
        return None
    columns = table.columns()
    if not columns:
        return None

    kinds = {name: column_type([entity.values.get(name)
                                for entity in table.entities])
             for name in columns}
    typed = sum(1 for kind, _unit, _currency in kinds.values() if kind != "text")
    confidence = typed / max(1, len(columns))

    cursor = store.conn.execute(
        """INSERT INTO x_tables
           (doc_id, chunk_id, name, source, n_rows, n_cols, confidence, verdict,
            extracted, kind)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (doc_id, None, table.type[:120], table.source, len(table.entities),
         len(columns), round(confidence, 3), "schema.org @type",
         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "jsonld"))
    table_id = int(cursor.lastrowid)

    store.conn.executemany(
        """INSERT INTO x_columns
           (table_id, position, name, type, unit, currency, null_rate, samples)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(table_id, position, name, kinds[name][0], kinds[name][1],
          kinds[name][2],
          round(sum(1 for e in table.entities if e.values.get(name) in (None, ""))
                / max(1, len(table.entities)), 3),
          json.dumps([str(e.values.get(name)) for e in table.entities[:3]],
                     ensure_ascii=False))
         for position, name in enumerate(columns)])

    store.conn.executemany(
        "INSERT INTO x_rows (table_id, row_number, data) VALUES (?,?,?)",
        [(table_id, number,
          json.dumps({name: coerce(entity.values.get(name), kinds[name][0])
                      for name in columns},
                     ensure_ascii=False, default=str))
         for number, entity in enumerate(table.entities, start=1)])

    return {"table_id": table_id, "name": table.type, "rows": len(table.entities),
            "kind": "jsonld", "confidence": round(confidence, 3),
            "columns": [{"name": name, "type": kinds[name][0],
                         "currency": kinds[name][2]} for name in columns]}


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


def extract_document(store, doc_id: int, raw: str, source: str, *,
                    dayfirst: bool = True) -> Dict[str, object]:
    """Tables and JSON-LD from an in-memory page, against a document already stored.

    This is what `harvest --extract` calls: the page was fetched, gated and
    stored, and its markup is still in hand, so there is no reason to re-read it
    from disk or to infer a table from flattened text.
    """
    ensure_schema(store)
    tables = tables_from_html(raw, source=source)

    # re-extracting a document must replace its records, not double them
    store.conn.execute("DELETE FROM x_tables WHERE doc_id = ?", (doc_id,))

    stored: List[Dict[str, object]] = []
    rejected = 0
    base = os.path.basename(source) or "page"
    for number, table in enumerate(tables, start=1):
        summary = store_table(store, table, doc_id, dayfirst=dayfirst,
                              name=f"{base}#{number}")
        if summary is None:
            rejected += 1
        else:
            stored.append(summary)

    from .jsonld import entities as jsonld_entities
    for entity_table in jsonld_entities(raw, source=source):
        summary = store_entity_table(store, entity_table, doc_id)
        if summary is None:
            rejected += 1
        else:
            stored.append(summary)

    equations = store_equations(store, doc_id, raw, source=source)
    store.commit()
    return {"tables": len(stored), "rows": sum(int(s["rows"]) for s in stored),
            "rejected": rejected, "equations": equations, "stored": stored}


def extract_path(store, path: str, *, licence: Optional[str] = None,
                 dayfirst: bool = True, verbose: bool = True) -> Dict[str, object]:
    """One file: HTML tables or a CSV, stored against a document."""
    from ..harvest.fetch import html_to_text

    suffix = os.path.splitext(path)[1].lower()
    raw = _read(path)
    ensure_schema(store)
    tables: List[Table] = []
    if suffix in (".html", ".htm", ".xhtml"):
        tables = tables_from_html(raw, source=path)
        _title, text, _links = html_to_text(raw)
    elif suffix in (".csv", ".tsv"):
        table = table_from_csv(raw, source=path)
        tables = [table] if table else []
        text = raw[:20000]
    elif suffix == ".pdf":
        from .pdf import extract_text_items, tables_from_pdf
        with open(path, "rb") as handle:
            data = handle.read()
        tables = tables_from_pdf(data, source=path)
        # the document text is every run, in reading order, so retrieval can
        # still find a paragraph that was not a table
        items = extract_text_items(data)
        text = "\n".join(item.text for item in
                         sorted(items, key=lambda i: (-i.y, i.x)))[:200000]
    else:
        return {"path": path, "tables": 0, "rows": 0, "skipped": "not html, csv or pdf"}

    doc_id = store.add_document(url=os.path.abspath(path),
                                title=os.path.basename(path), text=text,
                                licence=licence)
    if doc_id is None:
        return {"path": path, "tables": 0, "rows": 0, "skipped": "document not stored"}

    stored: List[Dict[str, object]] = []
    rejected = 0

    # re-extracting a document must replace its records, not double them
    store.conn.execute("DELETE FROM x_tables WHERE doc_id = ?", (doc_id,))

    for number, table in enumerate(tables, start=1):
        summary = store_table(store, table, doc_id, dayfirst=dayfirst,
                              name=f"{os.path.basename(path)}#{number}")
        if summary is None:
            rejected += 1
        else:
            stored.append(summary)

    if suffix in (".html", ".htm", ".xhtml"):
        from .jsonld import entities as jsonld_entities
        for entity_table in jsonld_entities(raw, source=path):
            summary = store_entity_table(store, entity_table, doc_id)
            if summary is None:
                rejected += 1
            else:
                stored.append(summary)
        equations = store_equations(store, doc_id, raw, source=path)
    else:
        equations = 0
    store.commit()

    if verbose:
        rows = sum(int(item["rows"]) for item in stored)
        print(f"  {os.path.basename(path)}: {len(stored)} table(s), {rows} row(s), "
              f"{equations} formula(s), {rejected} rejected")
    return {"path": path, "doc_id": doc_id, "tables": len(stored),
            "rows": sum(int(item["rows"]) for item in stored),
            "equations": equations,
            "rejected": rejected, "stored": stored}


def extract_dir(store, directory: str, *, licence: Optional[str] = None,
                dayfirst: bool = True, limit: int = 0,
                verbose: bool = True) -> Dict[str, object]:
    """Every html and csv under a directory, one document each."""
    files: List[str] = []
    for root, _dirs, names in os.walk(directory):
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() in (".html", ".htm", ".xhtml",
                                                     ".csv", ".tsv", ".pdf"):
                files.append(os.path.join(root, name))
    if limit:
        files = files[:limit]

    results = [extract_path(store, path, licence=licence, dayfirst=dayfirst,
                            verbose=verbose) for path in files]
    return {
        "files": len(results),
        "tables": sum(int(r["tables"]) for r in results),
        "rows": sum(int(r["rows"]) for r in results),
        "equations": sum(int(r.get("equations", 0)) for r in results),
        "rejected": sum(int(r.get("rejected", 0)) for r in results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# reading it back
# ---------------------------------------------------------------------------


def derive_table(store, table_id: int, spec: dict) -> Dict[str, object]:
    """Add computed columns to a table. Compiles first, writes second.

    Every formula is checked before any row is touched, so a bad spec fails
    once with nothing written rather than leaving half a table derived.
    """
    from .formula import FormulaError, compile_spec

    ensure_schema(store)
    existing = store.conn.execute(
        "SELECT name, position FROM x_columns WHERE table_id = ? ORDER BY position",
        (int(table_id),)).fetchall()
    if not existing:
        raise FormulaError(f"table {table_id} has no columns; extract it first")
    columns = [row["name"] for row in existing]
    derived = compile_spec(spec, columns)          # the security boundary

    rows = store.conn.execute(
        "SELECT row_number, data FROM x_rows WHERE table_id = ? ORDER BY row_number",
        (int(table_id),)).fetchall()
    if not rows:
        raise FormulaError(f"table {table_id} has no rows")

    nulls = {item.name: 0 for item in derived}
    updates = []
    for row in rows:
        values = json.loads(row["data"])
        for item in derived:
            try:
                value = item.compute(values)
            except FormulaError:
                value = None
            if value is None:
                nulls[item.name] += 1
            values[item.name] = value
        updates.append((json.dumps(values, ensure_ascii=False, default=str),
                        int(table_id), int(row["row_number"])))

    store.conn.executemany(
        "UPDATE x_rows SET data = ? WHERE table_id = ? AND row_number = ?", updates)

    position = max(int(row["position"]) for row in existing) + 1
    for item in derived:
        store.conn.execute(
            "INSERT OR REPLACE INTO x_columns "
            "(table_id, position, name, type, unit, currency, null_rate, samples) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (int(table_id), position, item.name, item.type, None, None,
             round(nulls[item.name] / max(1, len(rows)), 3),
             json.dumps([item.expr])))
        position += 1
    store.commit()

    return {"table_id": int(table_id), "rows": len(rows),
            "columns": [{"name": item.name, "expr": item.expr,
                         "guards": item.guards, "nulls": nulls[item.name]}
                        for item in derived]}


def apply_script(store, table_id: int, script_path: str, *, timeout: float = 5.0,
                 memory_mb: int = 512, isolation: str = "auto") -> Dict[str, object]:
    """Run a user script over a table's rows and store what it returns.

    The script is tier 2: real Python, in a child process, under the limits in
    `sandbox.py`. Every column it produces is stamped with the script's hash, so
    a value computed by code is never mistaken for one the page stated.
    """
    from .sandbox import run_script, script_hash

    ensure_schema(store)
    existing = store.conn.execute(
        "SELECT name, position FROM x_columns WHERE table_id = ? ORDER BY position",
        (int(table_id),)).fetchall()
    if not existing:
        raise ValueError(f"table {table_id} has no columns; extract it first")

    raw = store.conn.execute(
        "SELECT row_number, data FROM x_rows WHERE table_id = ? ORDER BY row_number",
        (int(table_id),)).fetchall()
    if not raw:
        raise ValueError(f"table {table_id} has no rows")
    rows = [json.loads(row["data"]) for row in raw]

    result = run_script(script_path, rows, timeout=timeout, memory_mb=memory_mb,
                        isolation=isolation)
    if not result.ok:
        return {"ok": False, "error": result.error, "run": result.to_dict()}

    origin = f"script:{script_hash(script_path)}"
    produced = sorted({key for row in result.rows for key in row})
    position = max(int(row["position"]) for row in existing) + 1
    nulls = {key: 0 for key in produced}
    updates = []

    for row, computed in zip(raw, result.rows):
        values = json.loads(row["data"])
        for key in produced:
            value = computed.get(key)
            if value is None:
                nulls[key] += 1
            values[key] = value
        updates.append((json.dumps(values, ensure_ascii=False, default=str),
                        int(table_id), int(row["row_number"])))

    store.conn.executemany(
        "UPDATE x_rows SET data = ? WHERE table_id = ? AND row_number = ?", updates)

    for key in produced:
        samples = [row.get(key) for row in result.rows[:3] if row.get(key) is not None]
        kind = "float"
        if samples and all(isinstance(v, bool) for v in samples):
            kind = "bool"
        elif samples and all(isinstance(v, int) and not isinstance(v, bool)
                             for v in samples):
            kind = "int"
        elif samples and all(isinstance(v, str) for v in samples):
            kind = "text"
        store.conn.execute(
            "INSERT OR REPLACE INTO x_columns "
            "(table_id, position, name, type, unit, currency, null_rate, samples, origin) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (int(table_id), position, key, kind, None, None,
             round(nulls[key] / max(1, len(rows)), 3),
             json.dumps(samples, ensure_ascii=False, default=str), origin))
        position += 1
    store.commit()

    return {"ok": True, "table_id": int(table_id), "rows": len(rows),
            "origin": origin, "run": result.to_dict(),
            "columns": [{"name": key, "nulls": nulls[key]} for key in produced]}


def store_equations(store, doc_id: int, html: str, source: str = "") -> int:
    """Every formula on a page, canonicalised and stored against the document.

    Re-extraction replaces a document's formulae rather than doubling them, for
    the same reason the tables do: `extract` is safe to run after a re-crawl.
    """
    from .mathquery import extract_math

    ensure_schema(store)
    equations = extract_math(html, source=source)
    store.conn.execute("DELETE FROM x_equations WHERE doc_id = ?", (doc_id,))
    if not equations:
        store.commit()
        return 0
    store.conn.executemany(
        "INSERT INTO x_equations "
        "(doc_id, chunk_id, latex, canonical, plain, symbols, display, source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(int(doc_id), None, item.latex, item.canonical, item.plain,
          json.dumps(item.symbols, ensure_ascii=False), 1 if item.display else 0,
          source) for item in equations])
    store.commit()
    return len(equations)


def search_equations(store, query: str, limit: int = 10,
                     min_overlap: float = 0.5) -> List[Dict[str, object]]:
    """Formulae matching a query, best first, each with its document and licence.

    Exact canonical match beats a subformula, which beats a symbol overlap —
    the order `match()` scores, so the ranking is the evidence and not a guess.
    """
    from .mathquery import Equation, match

    ensure_schema(store)
    rows = store.conn.execute(
        """SELECT e.equation_id, e.latex, e.canonical, e.plain, e.symbols,
                  e.display, d.url, d.title, d.licence, d.doc_id
           FROM x_equations e JOIN documents d ON d.doc_id = e.doc_id""").fetchall()

    hits = []
    for row in rows:
        equation = Equation(latex=row["latex"], canonical=row["canonical"],
                            plain=row["plain"] or "",
                            symbols=json.loads(row["symbols"] or "[]"),
                            source=row["url"] or "", display=bool(row["display"]))
        found = match(query, equation, min_overlap=min_overlap)
        if found is None:
            continue
        hits.append({"equation_id": int(row["equation_id"]),
                     "latex": row["latex"], "canonical": row["canonical"],
                     "plain": row["plain"], "score": found.score,
                     "kind": found.kind, "url": row["url"],
                     "title": row["title"], "licence": row["licence"],
                     "doc_id": int(row["doc_id"])})

    hits.sort(key=lambda hit: (-float(hit["score"]), hit["equation_id"]))
    return hits[:limit]


def list_equations(store, limit: int = 50) -> List[Dict[str, object]]:
    ensure_schema(store)
    rows = store.conn.execute(
        """SELECT e.latex, e.canonical, e.symbols, d.url, d.licence
           FROM x_equations e JOIN documents d ON d.doc_id = e.doc_id
           ORDER BY e.equation_id LIMIT ?""", (int(limit),)).fetchall()
    return [{"latex": row["latex"], "canonical": row["canonical"],
             "symbols": json.loads(row["symbols"] or "[]"),
             "url": row["url"], "licence": row["licence"]} for row in rows]


def list_tables(store) -> List[Dict[str, object]]:
    ensure_schema(store)
    rows = store.conn.execute(
        """SELECT t.table_id, t.name, t.n_rows, t.n_cols, t.confidence, t.verdict,
                  t.chunk_id, t.kind, d.url, d.licence
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
            "kind": row["kind"] or "html-table",
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
