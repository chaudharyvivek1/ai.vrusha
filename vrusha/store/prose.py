"""Entities from prose: the store could read tables and not sentences.

`relations.py` reads columns and `entities.py` resolves spellings across them.
Neither can look at a paragraph and say "that is an organisation" — so a corpus of
prose produced no entities at all, which is what the outside review meant by graph
intelligence being 5/10.

This is a **rule-based extractor**, and the choice is deliberate: a small NER
model is 100–400 MB and another dependency, and this project's rule is that the
core stays numpy-only. The rules below are the ones a careful reader uses —
capitalisation, organisation suffixes, honorifics, prepositional context — and
they are wrong in ways that are written down rather than hidden.

What it will do: `Acme Corporation`, `Dr. Meera Rao`, `Pune`, `the Reserve Bank of
India`.
What it will not do: recognise an organisation with no suffix and no capital
(`acme`), disambiguate `Jordan` the person from `Jordan` the country, or find a
name that is only ever written in lowercase. Those are model jobs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence

#: Suffixes that make a capitalised run an organisation even at a sentence start.
ORG_SUFFIXES = frozenset("""
ltd limited inc incorporated corp corporation company co llc llp plc gmbh pvt
private pte bv sa ag university institute college school bank hospital trust
foundation council ministry department authority board committee association
society group holdings industries technologies systems solutions labs services
""".split())

#: Words that introduce a person.
HONORIFICS = frozenset("mr mrs ms dr prof sir madam shri smt".split())

#: Prepositions that introduce a place.
PLACE_PREPS = frozenset("in at near from to across throughout".split())

#: Lowercase words that belong inside a name: `Reserve Bank of India`.
CONNECTORS = frozenset("of the and & de van von da bin".split())

_WORD = re.compile(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F'’\-]*")
_SENTENCE = re.compile(r"[.!?;:]\s")


@dataclass
class Entity:
    text: str
    kind: str            # organisation | person | place | proper
    start: int
    end: int
    confidence: float

    def to_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind, "start": self.start,
                "end": self.end, "confidence": round(self.confidence, 2)}


def _is_capitalised(token: str) -> bool:
    stripped = token.lstrip("([\"'")
    return bool(stripped) and stripped[0].isupper()


def _clean(token: str) -> str:
    return token.strip(".,;:()[]\"'’")


def _sentence_start(text: str, position: int) -> bool:
    """True when only whitespace and a sentence break sit before `position`."""
    before = text[:position]
    return not before.strip() or bool(_SENTENCE.search(before[-3:]))


def extract(text: str) -> List[Entity]:
    """Every entity the rules can see, in reading order."""
    if not text:
        return []
    tokens = [(match.group(0), match.start(), match.end())
              for match in _WORD.finditer(text)]
    found: List[Entity] = []
    index = 0
    while index < len(tokens):
        token, start, end = tokens[index]
        word = _clean(token)
        lower = word.lower().rstrip(".")

        if not _is_capitalised(token):
            index += 1
            continue

        # a run of consecutive capitalised words, at most four
        run = [word]
        run_start, run_end = start, end
        cursor = index + 1
        while cursor < len(tokens) and len(run) < 5:
            nxt, nxt_start, nxt_end = tokens[cursor]
            # a sentence break ends the run: `Pune. Dr Meera Rao` is two entities,
            # not one -- found by running it, because the period sat inside the
            # previous token and the whitespace check could not see it
            if _sentence_start(text, nxt_start):
                break
            if _is_capitalised(nxt):
                run.append(_clean(nxt))
            elif _clean(nxt).lower() in CONNECTORS and cursor + 1 < len(tokens):
                # `of`, `the`, `and` inside a name, but never at the end of one
                if not _is_capitalised(tokens[cursor + 1][0]):
                    break
                run.append(_clean(nxt).lower())
            else:
                break
            run_end = nxt_end
            cursor += 1

        run_lower = [part.lower() for part in run]
        at_start = _sentence_start(text, run_start)
        previous = _clean(tokens[index - 1][0]).lower() if index else ""

        kind, confidence = "proper", 0.4
        if any(part in ORG_SUFFIXES for part in run_lower):
            kind, confidence = "organisation", 0.9
        elif previous in HONORIFICS:
            kind, confidence = "person", 0.85
        elif previous in PLACE_PREPS:
            kind, confidence = "place", 0.7
        elif len(run) >= 2 and not at_start:
            # two capitalised words mid-sentence: a name of some kind
            kind, confidence = "proper", 0.6
        elif at_start and len(run) == 1:
            # a sentence-initial single capital is almost always just a word
            index = cursor
            continue
        elif len(run) >= 2:
            kind, confidence = "proper", 0.5

        # a leading article is not part of the name: `The Reserve Bank of India`
        # is `Reserve Bank of India`
        while len(run) > 1 and run[0].lower() in ("the", "a", "an"):
            run.pop(0)
        surface = " ".join(run)
        if len(surface) > 2:
            found.append(Entity(text=surface, kind=kind, start=run_start,
                                end=run_end, confidence=confidence))
        index = cursor
    return found


def dedupe(entities: Sequence[Entity]) -> List[Entity]:
    """One entry per surface form, keeping the most confident kind."""
    best: Dict[str, Entity] = {}
    for entity in entities:
        key = entity.text.lower()
        current = best.get(key)
        if current is None or entity.confidence > current.confidence:
            best[key] = entity
    return sorted(best.values(), key=lambda item: item.start)


def by_kind(entities: Sequence[Entity]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for entity in entities:
        counts[entity.kind] = counts.get(entity.kind, 0) + 1
    return counts
