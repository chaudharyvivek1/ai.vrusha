"""Relations between entities, with the sentence that asserted them.

`prose.py` finds entities; a list of names is not a graph. An edge needs three
things, and most extractors stop after the second:

1. a **subject** and an **object** — entities already found;
2. a **predicate** — what relates them;
3. **evidence** — the sentence that said so, kept verbatim.

The third is the one that makes a graph usable. An edge without its evidence
cannot be checked, and a knowledge graph you cannot check is a rumour with a
schema. Every edge this module produces carries the sentence it came from, the
span of that sentence, and a confidence that says how strong the pattern was.

It is pattern-based, deliberately: `Acme acquired Beta`, `Acme supplies Beta`,
`Acme is a subsidiary of Beta`. A verb that is not in the list produces no edge —
a missing edge is a gap, an invented edge is a wrong answer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .prose import Entity

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_edges (
  edge_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id     INTEGER NOT NULL DEFAULT 0,
  chunk_id   INTEGER NOT NULL DEFAULT 0,
  subject    TEXT NOT NULL,
  predicate  TEXT NOT NULL,
  object     TEXT NOT NULL,
  sentence   TEXT NOT NULL,
  confidence REAL NOT NULL,
  created    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_subject ON x_edges(subject);
CREATE INDEX IF NOT EXISTS idx_edges_object  ON x_edges(object);
"""

#: predicate -> the patterns that produce it. Each is a phrase between two
#: entities, matched case-insensitively on the words between them.
PATTERNS: List[tuple] = [
    ("acquired", r"\b(acquired|bought|took over|purchased)\b", 0.9),
    ("supplies", r"\b(supplies|supplied|provides|sells to|sold to)\b", 0.85),
    ("subsidiary_of", r"\b(is a subsidiary of|is owned by|is a division of)\b", 0.9),
    ("partnered_with", r"\b(partnered with|teamed up with|joint venture with)\b", 0.8),
    ("appointed", r"\b(appointed|hired|named)\b", 0.8),
    ("located_in", r"\b(is based in|is located in|operates in|headquartered in)\b", 0.75),
    ("reported", r"\b(reported|announced|disclosed|posted)\b", 0.7),
    ("competes_with", r"\b(competes with|rivals|is a competitor of)\b", 0.75),
    ("works_for", r"\b(works for|joined|employed by)\b", 0.8),
]

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

#: A relation cannot hang off a date. `in March` is a place by the preposition
#: rule, and `Acme acquired March` was the first false edge this module produced
#: -- so the calendar is a stoplist rather than a smarter rule.
_NOT_AN_ARGUMENT = frozenset("""
january february march april may june july august september october november
december monday tuesday wednesday thursday friday saturday sunday today
yesterday tomorrow monday-sunday
""".split())


@dataclass
class Edge:
    subject: str
    predicate: str
    object: str
    sentence: str
    confidence: float
    doc_id: int = 0
    chunk_id: int = 0

    def to_dict(self) -> dict:
        return {"subject": self.subject, "predicate": self.predicate,
                "object": self.object, "sentence": self.sentence,
                "confidence": round(self.confidence, 2),
                "doc_id": self.doc_id, "chunk_id": self.chunk_id}


def sentences(text: str) -> List[tuple]:
    """(sentence, offset) pairs. The offset is what makes evidence checkable."""
    found, start = [], 0
    for match in _SENTENCE_END.finditer(text):
        found.append((text[start:match.start()], start))
        start = match.end()
    if start < len(text):
        found.append((text[start:], start))
    return [(sentence.strip(), offset + (len(sentence) - len(sentence.lstrip())))
            for sentence, offset in found if sentence.strip()]


def extract(text: str, entities: Optional[Sequence[Entity]] = None) -> List[Edge]:
    """Edges from one piece of text. Two entities and a known verb, or nothing."""
    from .prose import extract as extract_entities

    if not text:
        return []
    entities = list(entities if entities is not None else extract_entities(text))
    if len(entities) < 2:
        return []

    # A month, a weekday or a bare capitalised word is not an entity a relation
    # can hang off. `Acme acquired March` was the first false edge this produced;
    # the fix is to require the extractor's own confidence, not to add a calendar.
    usable = [entity for entity in entities
              if entity.confidence >= 0.5
              and entity.text.lower().strip(".") not in _NOT_AN_ARGUMENT]
    entities = usable

    # Two spellings of one company were two nodes. Resolution was built in wave 26
    # for exactly this and never wired to the edges that needed it -- found on a
    # real corpus, where `Technologies` and `Alpha Design Technologies Pvt Ltd`
    # both appeared as subjects.
    from .entities import canonical_map
    mapping = canonical_map([entity.text for entity in usable])
    for entity in usable:
        entity.text = mapping.get(entity.text, entity.text)
    edges: List[Edge] = []
    seen: set = set()
    for sentence, offset in sentences(text):
        # entities that fall inside this sentence
        inside = [entity for entity in entities
                  if offset <= entity.start and entity.end <= offset + len(sentence)]
        if len(inside) < 2:
            continue
        for left_index, left in enumerate(inside):
            for right in inside[left_index + 1:]:
                if left.end > right.start:
                    continue
                between = text[left.end:right.start]
                if len(between) > 120:
                    continue
                for predicate, pattern, confidence in PATTERNS:
                    if not re.search(pattern, between, re.IGNORECASE):
                        continue
                    key = (left.text.lower(), predicate, right.text.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append(Edge(subject=left.text, predicate=predicate,
                                      object=right.text, sentence=sentence,
                                      confidence=confidence))
                    break
    return edges


def ensure_schema(store) -> None:
    store.conn.executescript(SCHEMA)


def store_edges(store, edges: Sequence[Edge], *, doc_id: int = 0,
                chunk_id: int = 0) -> int:
    """Persist the edges, evidence included. Replaces what this doc had."""
    ensure_schema(store)
    import time
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if doc_id:
        store.conn.execute("DELETE FROM x_edges WHERE doc_id = ?", (doc_id,))
    written = 0
    for edge in edges:
        store.conn.execute(
            "INSERT INTO x_edges (doc_id, chunk_id, subject, predicate, object, "
            "sentence, confidence, created) VALUES (?,?,?,?,?,?,?,?)",
            (int(doc_id or edge.doc_id), int(chunk_id or edge.chunk_id),
             edge.subject, edge.predicate, edge.object, edge.sentence,
             float(edge.confidence), stamp))
        written += 1
    store.commit()
    return written


def list_edges(store, *, subject: str = "", limit: int = 100) -> List[dict]:
    ensure_schema(store)
    if subject:
        rows = store.conn.execute(
            "SELECT * FROM x_edges WHERE lower(subject) = lower(?) "
            "ORDER BY confidence DESC LIMIT ?", (subject, limit)).fetchall()
    else:
        rows = store.conn.execute(
            "SELECT * FROM x_edges ORDER BY confidence DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(row) for row in rows]


def neighbours(store, name: str, *, limit: int = 20) -> List[dict]:
    """Everything one hop from a name, in either direction, with evidence."""
    ensure_schema(store)
    rows = store.conn.execute(
        "SELECT * FROM x_edges WHERE lower(subject) = lower(?) "
        "OR lower(object) = lower(?) ORDER BY confidence DESC LIMIT ?",
        (name, name, limit)).fetchall()
    return [dict(row) for row in rows]
