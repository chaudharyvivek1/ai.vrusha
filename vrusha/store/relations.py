"""Relations between extracted tables: which columns are the same entity.

Six tables extracted from six pages are six piles of rows until something says
`sku` in one is `sku` in another. That is what this module does, and it does it
without a model:

    a key column    unique, non-null, short, at least two distinct values
    a join          two key columns whose value sets overlap enough to be the
                    same entity, scored by Jaccard and stored with the number

The stored number is the point. A join with `overlap: 1.0, shared: 3` is a fact
you can act on; a join with `overlap: 0.4, shared: 2` is a coincidence you
should look at before trusting. So nothing joins automatically — the edges are
written to `x_joins` with their evidence, and `vrusha joins` shows them for a
human to accept.

What this deliberately does not do: entity resolution (two spellings of one
vendor), transitive closure across a chain of joins, or fuzzy matching. Those
are separate problems, and each of them can be wrong in a way that silently
merges two companies into one.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .extract import ascii_digits

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_joins (
  left_table  INTEGER NOT NULL REFERENCES x_tables(table_id) ON DELETE CASCADE,
  left_column TEXT NOT NULL,
  right_table INTEGER NOT NULL REFERENCES x_tables(table_id) ON DELETE CASCADE,
  right_column TEXT NOT NULL,
  shared      INTEGER NOT NULL,
  overlap     REAL NOT NULL,
  kind        TEXT NOT NULL,
  inferred    TEXT NOT NULL,
  PRIMARY KEY (left_table, left_column, right_table, right_column)
);
CREATE INDEX IF NOT EXISTS idx_x_joins_left  ON x_joins(left_table);
CREATE INDEX IF NOT EXISTS idx_x_joins_right ON x_joins(right_table);
"""

#: A key column's values are short. A 500-character "value" that happens to be
#: unique is a description, and joining two descriptions is a coincidence.
_MAX_KEY_LEN = 64

#: Below this Jaccard the pair is noise. Two tables that share one value out of
#: twenty are not related; they both mention a country.
_MIN_OVERLAP = 0.5

#: Keys must be nearly unique inside their own table, or they are categories.
_MIN_UNIQUENESS = 0.9

#: Columns whose name says "this identifies a thing". A column called `sku` is a
#: key even when it is numeric; a column called `price` is a measure even when
#: every value is unique.
_IDENTITY_NAME = re.compile(
    r"(^|_)(id|ids|sku|code|key|slug|ref|isbn|ean|upc|url|name|title|vendor|"
    r"product|plan|model|serial|handle)($|_)")

#: A column that is mostly numbers is a measure, not an identifier, unless its
#: name says otherwise. Two tables of the same three prices are not the same
#: entity; they are two tables of the same three products.
_MEASURE_RATIO = 0.8


@dataclass
class Candidate:
    table_id: int
    table_name: str
    column: str
    values: Set[str]
    rows: int

    @property
    def label(self) -> str:
        return f"{self.table_name}.{self.column}"


@dataclass
class Join:
    left: Candidate
    right: Candidate
    shared: int
    overlap: float
    kind: str

    def to_dict(self) -> dict:
        return {
            "left_table": self.left.table_id, "left_name": self.left.table_name,
            "left_column": self.left.column,
            "right_table": self.right.table_id, "right_name": self.right.table_name,
            "right_column": self.right.column,
            "shared": self.shared, "overlap": round(self.overlap, 4),
            "kind": self.kind,
        }


def ensure_schema(store) -> None:
    store.conn.executescript(SCHEMA)


def normalise(value: object) -> str:
    """Compare values as a person would: case, spacing and digits folded.

    `SKU-100` and `sku 100` are the same key. `१००` and `100` are the same
    number. Everything else is left alone, because normalising further is how
    two different products become one row.
    """
    if value is None:
        return ""
    text = ascii_digits(str(value)).strip().lower()
    # every separator becomes one space: `SKU-100`, `sku_100` and `sku 100` are
    # the same key, but `AB` and `A B` stay different
    text = re.sub(r"[\s_\-–—/]+", " ", text)
    return text.strip(" .")


# ---------------------------------------------------------------------------
# what can be a key
# ---------------------------------------------------------------------------


def _columns(store, table_id: int) -> List[str]:
    return [row["name"] for row in store.conn.execute(
        "SELECT name FROM x_columns WHERE table_id = ? ORDER BY position",
        (int(table_id),)).fetchall()]


def _rows(store, table_id: int) -> List[dict]:
    rows = store.conn.execute(
        "SELECT data FROM x_rows WHERE table_id = ? ORDER BY row_number",
        (int(table_id),)).fetchall()
    output = []
    for row in rows:
        try:
            output.append(json.loads(row["data"]))
        except (TypeError, ValueError):
            continue
    return output


def _looks_like_measure(values: Set[str]) -> bool:
    """True when the values are numbers or dates rather than identifiers.

    A unique number in a table is almost always a quantity. Two tables sharing
    the value `123456` share a number, not a thing.
    """
    if not values:
        return False
    numeric = 0
    for value in values:
        stripped = value.replace(",", "").replace("%", "").strip()
        try:
            float(stripped)
            numeric += 1
            continue
        except ValueError:
            pass
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(t[\d:]+)?", value):
            numeric += 1
    return numeric / len(values) >= _MEASURE_RATIO


def key_columns(store, table_id: int, table_name: str = "",
                *, min_rows: int = 2) -> List[Candidate]:
    """Columns of one table that could identify a row."""
    rows = _rows(store, table_id)
    if len(rows) < min_rows:
        return []
    name = table_name or str(table_id)
    output: List[Candidate] = []

    for column in _columns(store, table_id):
        values = [row.get(column) for row in rows]
        filled = [value for value in values if value not in (None, "")]
        if len(filled) < len(values) * 0.95:
            continue                      # a key with gaps is not a key
        normalised = {normalise(value) for value in filled}
        normalised.discard("")
        if len(normalised) < min_rows:
            continue                      # one value repeated is a category
        if len(normalised) < len(filled) * _MIN_UNIQUENESS:
            continue                      # duplicates: this is not an identifier
        if any(len(value) > _MAX_KEY_LEN for value in normalised):
            continue                      # a description, not a key
        if not _IDENTITY_NAME.search(column) and _looks_like_measure(normalised):
            continue                      # price, uptime, a date: a measure
        output.append(Candidate(table_id=int(table_id), table_name=name,
                                column=column, values=normalised,
                                rows=len(rows)))
    return output


def candidates(store, *, limit_tables: int = 50) -> List[Candidate]:
    """Every key candidate in the store, table by table."""
    tables = store.conn.execute(
        "SELECT table_id, name FROM x_tables ORDER BY table_id LIMIT ?",
        (int(limit_tables),)).fetchall()
    output: List[Candidate] = []
    for row in tables:
        output.extend(key_columns(store, int(row["table_id"]), row["name"]))
    return output


# ---------------------------------------------------------------------------
# which pairs are the same entity
# ---------------------------------------------------------------------------


def infer_joins(store, *, min_overlap: float = _MIN_OVERLAP,
                min_shared: int = 2, limit_tables: int = 50) -> List[Join]:
    """Every pair of key columns whose values overlap enough to be one entity.

    Symmetric pairs are reported once, and a column is never joined to itself
    in the same table.
    """
    pool = candidates(store, limit_tables=limit_tables)
    joins: List[Join] = []

    for index, left in enumerate(pool):
        for right in pool[index + 1:]:
            if left.table_id == right.table_id:
                continue
            shared = left.values & right.values
            if len(shared) < min_shared:
                continue
            union = len(left.values | right.values)
            overlap = len(shared) / max(1, union)
            if overlap < min_overlap:
                continue
            kind = ("same-column" if left.column == right.column
                    else "value-match")
            joins.append(Join(left=left, right=right, shared=len(shared),
                              overlap=overlap, kind=kind))

    joins.sort(key=lambda join: (-join.overlap, -join.shared))
    return joins


def store_joins(store, joins: Sequence[Join]) -> int:
    """Write the edges, replacing what was there. Returns how many were kept."""
    ensure_schema(store)
    store.conn.execute("DELETE FROM x_joins")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    written = 0
    for join in joins:
        # store each pair once, in a stable order, so a re-run is identical
        left, right = join.left, join.right
        if (left.table_id, left.column) > (right.table_id, right.column):
            left, right = right, left
        store.conn.execute(
            "INSERT OR REPLACE INTO x_joins "
            "(left_table, left_column, right_table, right_column, shared, "
            " overlap, kind, inferred) VALUES (?,?,?,?,?,?,?,?)",
            (left.table_id, left.column, right.table_id, right.column,
             join.shared, round(join.overlap, 4), join.kind, stamp))
        written += 1
    store.commit()
    return written


def list_joins(store, table_id: int = 0) -> List[dict]:
    ensure_schema(store)
    query = """
        SELECT j.left_table, j.left_column, j.right_table, j.right_column,
               j.shared, j.overlap, j.kind, j.inferred,
               l.name AS left_name, r.name AS right_name
        FROM x_joins j
        JOIN x_tables l ON l.table_id = j.left_table
        JOIN x_tables r ON r.table_id = j.right_table
    """
    params: Tuple = ()
    if table_id:
        query += " WHERE j.left_table = ? OR j.right_table = ?"
        params = (int(table_id), int(table_id))
    query += " ORDER BY j.overlap DESC, j.shared DESC"
    rows = store.conn.execute(query, params).fetchall()
    return [{
        "left_table": int(row["left_table"]), "left_name": row["left_name"],
        "left_column": row["left_column"],
        "right_table": int(row["right_table"]), "right_name": row["right_name"],
        "right_column": row["right_column"],
        "shared": int(row["shared"]), "overlap": float(row["overlap"]),
        "kind": row["kind"], "inferred": row["inferred"],
        "expression": (f"{row['left_name']}.{row['left_column']} = "
                       f"{row['right_name']}.{row['right_column']}"),
    } for row in rows]


def build(store, *, min_overlap: float = _MIN_OVERLAP,
          min_shared: int = 2) -> dict:
    """Infer, store, and report. What `vrusha joins --build` calls."""
    joins = infer_joins(store, min_overlap=min_overlap, min_shared=min_shared)
    written = store_joins(store, joins)
    return {"joins": written,
            "edges": [join.to_dict() for join in joins],
            "tables": store.conn.execute(
                "SELECT COUNT(*) FROM x_tables").fetchone()[0]}
