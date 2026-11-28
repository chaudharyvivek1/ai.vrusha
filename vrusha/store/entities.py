"""Entity resolution: `Acme Corp` and `ACME Corporation` are one vendor.

`relations.py` finds joins between columns that share *exact* values, and says in
its own docstring that it does not do entity resolution — because merging two
companies into one is the kind of mistake that looks like a feature. This is that
step, done with the same rule: the evidence is stored, nothing is merged silently,
and a human can see every alias and the score that put it in a group.

The algorithm is deliberately dull:

    block      group candidate values by their first three characters, so the
               comparison is not every value against every other
    score      `token_set_ratio` — `Acme Corp` against `ACME Corporation`
    merge      union-find over pairs at or above the threshold
    canonical  the alias closest to everything else in its group
    keep       groups of one are discarded; they are not entities, they are rows

What it still will not do: tell you that `Acme` and `Beta Holdings` are the same
company because both are in Pune. That is a judgement about the world, not a
string, and a system that guesses it will eventually be confidently wrong.
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from . import relations

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_entities (
  entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical TEXT NOT NULL,
  size      INTEGER NOT NULL,
  resolved  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS x_entity_aliases (
  entity_id INTEGER NOT NULL REFERENCES x_entities(entity_id) ON DELETE CASCADE,
  alias     TEXT NOT NULL,
  score     REAL NOT NULL,
  surfaces  INTEGER NOT NULL,
  PRIMARY KEY (entity_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_x_alias ON x_entity_aliases(alias);
"""

#: Above this the pair is the same entity as far as a string can tell. 90 is
#: high on purpose: `token_set_ratio` gives `Acme Corp` against `Acme` a high
#: score too, and "Acme" may well be a different company.
DEFAULT_THRESHOLD = 90.0

#: Two characters of blocking: enough to keep the comparison count sane, loose
#: enough that `ACME` and `Acme` land in the same block.
BLOCK_PREFIX = 3


#: Abbreviations that make two spellings of one name look different. Expanded
#: before scoring, because no string metric knows that `corp` means
#: `corporation` — `token_set_ratio` scores `acme corp` against
#: `acme corporation` at 72 and against `acme` at 100, which is backwards.
ABBREVIATIONS = {
    "corp": "corporation", "co": "company", "ltd": "limited",
    "pvt": "private", "inc": "incorporated", "mfg": "manufacturing",
    "intl": "international", "svc": "services", "svcs": "services",
    "tech": "technologies", "univ": "university", "dept": "department",
    "govt": "government", "assoc": "association", "bros": "brothers",
}


def _surface(value: str) -> str:
    """Normalise, then expand abbreviations token by token."""
    text = relations.normalise(value)
    tokens = [token.strip(".") for token in text.split()]
    return " ".join(ABBREVIATIONS.get(token, token) for token in tokens if token)


def _ratio(first: str, second: str) -> float:
    """Similarity in 0..100 on the *normalised* forms.

    `ratio`, not `token_set_ratio`: a set metric scores `acme` against
    `acme corporation` at 100 because one set contains the other, and that is
    how two different companies get merged. Character ratio after abbreviation
    expansion is conservative — it misses some true pairs, which is the safe
    direction.
    """
    left, right = _surface(first), _surface(second)
    try:
        from rapidfuzz import fuzz
        return float(fuzz.ratio(left, right))
    except Exception:                        # noqa: BLE001 - optional dependency
        from difflib import SequenceMatcher
        return 100.0 * SequenceMatcher(None, left, right).ratio()


def ensure_schema(store) -> None:
    store.conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# gathering the candidates
# ---------------------------------------------------------------------------


def _surfaces(store) -> Dict[str, int]:
    """Normalised value -> how many key columns contain it.

    Not "how many rows": a key column has unique values by definition, so the
    two counts are always the same number. Counting the set is O(1) per value;
    reading every row to arrive at the same answer would be a worse trade.
    """
    counts: Counter = Counter()
    for candidate in relations.candidates(store):
        for value in candidate.values:
            counts[value] += 1
    return dict(counts)


def _block_key(value: str) -> str:
    """A cheap blocking key: letters and digits only, first three characters."""
    compact = re.sub(r"[^0-9a-z\u0900-\u097F]+", "", value)
    return compact[:BLOCK_PREFIX] or value[:1]


class _Union:
    """Union-find, because a chain of pairs is one group, not many."""

    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


# ---------------------------------------------------------------------------
# resolving
# ---------------------------------------------------------------------------


def resolve(store, *, threshold: float = DEFAULT_THRESHOLD,
            min_size: int = 2) -> List[dict]:
    """Groups of surface forms that are the same entity, with their scores."""
    surfaces = _surfaces(store)
    if len(surfaces) < 2:
        return []

    blocks: Dict[str, List[str]] = defaultdict(list)
    for value in surfaces:
        blocks[_block_key(value)].append(value)

    union = _Union()
    best: Dict[Tuple[str, str], float] = {}
    for values in blocks.values():
        if len(values) < 2:
            continue
        for index, first in enumerate(values):
            for second in values[index + 1:]:
                score = _ratio(first, second)
                if score >= threshold:
                    union.union(first, second)
                    best[(first, second)] = score
                    best[(second, first)] = score

    groups: Dict[str, List[str]] = defaultdict(list)
    for value in surfaces:
        groups[union.find(value)].append(value)

    entities: List[dict] = []
    for members in groups.values():
        if len(members) < min_size:
            continue
        # the canonical form is the member closest to everything else in the
        # group; ties go to the longest, which is usually the spelled-out one
        def closeness(value: str) -> Tuple[float, int]:
            others = [other for other in members if other != value]
            total = sum(_ratio(value, other) for other in others) / max(1, len(others))
            return (total, len(value))

        canonical = max(members, key=closeness)
        aliases = []
        for value in members:
            if value == canonical:
                continue
            score = best.get((canonical, value))
            if score is None:                 # joined through a third member
                score = _ratio(canonical, value)
            aliases.append({"alias": value, "score": round(float(score), 1),
                            "surfaces": surfaces.get(value, 0)})
        aliases.sort(key=lambda item: (-item["score"], item["alias"]))
        entities.append({"canonical": canonical,
                         "surfaces": surfaces.get(canonical, 0),
                         "size": len(members), "aliases": aliases})

    entities.sort(key=lambda item: (-item["size"], item["canonical"]))
    return entities


def store_entities(store, entities: Sequence[dict]) -> int:
    """Write the groups, replacing what was there."""
    ensure_schema(store)
    store.conn.execute("DELETE FROM x_entity_aliases")
    store.conn.execute("DELETE FROM x_entities")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    written = 0
    for entity in entities:
        cursor = store.conn.execute(
            "INSERT INTO x_entities (canonical, size, resolved) VALUES (?,?,?)",
            (entity["canonical"], int(entity["size"]), stamp))
        entity_id = int(cursor.lastrowid)
        store.conn.execute(
            "INSERT INTO x_entity_aliases (entity_id, alias, score, surfaces) "
            "VALUES (?,?,?,?)",
            (entity_id, entity["canonical"], 100.0,
             int(entity.get("surfaces", 0))))
        for alias in entity["aliases"]:
            store.conn.execute(
                "INSERT INTO x_entity_aliases (entity_id, alias, score, surfaces) "
                "VALUES (?,?,?,?)",
                (entity_id, alias["alias"], float(alias["score"]),
                 int(alias["surfaces"])))
        written += 1
    store.commit()
    return written


def build(store, *, threshold: float = DEFAULT_THRESHOLD,
          min_size: int = 2) -> dict:
    """Resolve, store, and report. What `vrusha entities --build` calls."""
    entities = resolve(store, threshold=threshold, min_size=min_size)
    written = store_entities(store, entities)
    return {"entities": written, "threshold": threshold,
            "groups": entities,
            "aliases": sum(1 for entity in entities for _ in entity["aliases"])}


def list_entities(store) -> List[dict]:
    ensure_schema(store)
    rows = store.conn.execute(
        "SELECT entity_id, canonical, size, resolved FROM x_entities "
        "ORDER BY size DESC, canonical").fetchall()
    output = []
    for row in rows:
        aliases = store.conn.execute(
            "SELECT alias, score, surfaces FROM x_entity_aliases "
            "WHERE entity_id = ? AND alias != ? ORDER BY score DESC",
            (row["entity_id"], row["canonical"])).fetchall()
        output.append({
            "entity_id": int(row["entity_id"]), "canonical": row["canonical"],
            "size": int(row["size"]), "resolved": row["resolved"],
            "aliases": [{"alias": alias["alias"], "score": float(alias["score"]),
                         "surfaces": int(alias["surfaces"])} for alias in aliases],
        })
    return output


def canonical_of(store, value: str) -> Optional[str]:
    """The canonical form of one value, or None when it was not resolved."""
    ensure_schema(store)
    row = store.conn.execute(
        "SELECT e.canonical FROM x_entity_aliases a "
        "JOIN x_entities e ON e.entity_id = a.entity_id "
        "WHERE a.alias = ? LIMIT 1", (relations.normalise(value),)).fetchone()
    return str(row[0]) if row else None
