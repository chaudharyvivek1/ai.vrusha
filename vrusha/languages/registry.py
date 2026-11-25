"""Language profiles: what the system knows about a language before it reads it.

Two kinds live here and they are treated the same way — a profile is a file of
facts, and the code asks the profile instead of hard-coding an if-statement:

  * natural languages  (en, hi, mr, ta, bn) — script, direction, sentence ends,
    stopwords, and the grammar story: word order, morphology, how the language
    carries meaning.
  * programming languages (python, javascript, ...) — extensions, comment and
    string syntax, indentation, and how to split it into units.

Adding a language is adding a JSON file to `profiles/`. Nothing else changes.
"""
from __future__ import annotations

import glob
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")

# Unicode script ranges, coarse but sufficient for the languages carried here.
SCRIPT_RANGES: Dict[str, Tuple[int, int]] = {
    "Latn": (0x0041, 0x024F),
    "Deva": (0x0900, 0x097F),
    "Taml": (0x0B80, 0x0BFF),
    "Beng": (0x0980, 0x09FF),
    "Guru": (0x0A00, 0x0A7F),
    "Telu": (0x0C00, 0x0C7F),
    "Knda": (0x0C80, 0x0CFF),
    "Mlym": (0x0D00, 0x0D7F),
    "Gujr": (0x0A80, 0x0AFF),
    "Orya": (0x0B00, 0x0B7F),
    "Arab": (0x0600, 0x06FF),
    "Cyrl": (0x0400, 0x04FF),
    "Hani": (0x4E00, 0x9FFF),
}


class LanguageError(ValueError):
    """A profile is malformed or a language is unknown."""


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    name: str
    kind: str                       # "natural" | "code"
    family_id: int                  # the address's second segment
    script: str = "Latn"
    direction: str = "ltr"
    extensions: tuple[str, ...] = ()
    stopwords: tuple[str, ...] = ()
    sentence_end: tuple[str, ...] = (".", "!", "?")
    comment: tuple[str, ...] = ()
    strings: tuple[str, ...] = ()
    indent: str = "spaces"
    chunk_strategy: str = "paragraph"   # ast | delimiter | paragraph | line
    grammar: Dict[str, str] = field(default_factory=dict)
    markers: tuple[str, ...] = ()
    notes: str = ""

    @property
    def is_code(self) -> bool:
        return self.kind == "code"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "kind": self.kind,
            "family_id": self.family_id,
            "script": self.script,
            "direction": self.direction,
            "extensions": list(self.extensions),
            "chunk_strategy": self.chunk_strategy,
            "grammar": self.grammar,
        }


def _profile_from(raw: dict, source: str) -> LanguageProfile:
    for required in ("code", "name", "kind", "family_id"):
        if required not in raw:
            raise LanguageError(f"{source}: missing {required!r}")
    if raw["kind"] not in ("natural", "code"):
        raise LanguageError(f"{source}: kind must be natural or code")
    return LanguageProfile(
        code=str(raw["code"]),
        name=str(raw["name"]),
        kind=str(raw["kind"]),
        family_id=int(raw["family_id"]),
        script=str(raw.get("script", "Latn")),
        direction=str(raw.get("direction", "ltr")),
        extensions=tuple(raw.get("extensions", [])),
        stopwords=tuple(raw.get("stopwords", [])),
        sentence_end=tuple(raw.get("sentence_end", [".", "!", "?"])),
        comment=tuple(raw.get("comment", [])),
        strings=tuple(raw.get("strings", [])),
        indent=str(raw.get("indent", "spaces")),
        chunk_strategy=str(raw.get("chunk_strategy", "paragraph")),
        grammar=dict(raw.get("grammar", {})),
        markers=tuple(raw.get("markers", [])),
        notes=str(raw.get("notes", "")),
    )


class Registry:
    """Every profile, indexed by code, extension and family id."""

    def __init__(self, profiles: List[LanguageProfile], root: str = ""):
        self.root = root
        self._by_code: Dict[str, LanguageProfile] = {}
        self._by_ext: Dict[str, LanguageProfile] = {}
        for profile in profiles:
            if profile.code in self._by_code:
                raise LanguageError(f"duplicate profile {profile.code}")
            self._by_code[profile.code] = profile
            for ext in profile.extensions:
                ext = ext if ext.startswith(".") else "." + ext
                self._by_ext[ext.lower()] = profile

    @classmethod
    def load(cls, root: Optional[str] = None) -> "Registry":
        root = root or PROFILE_DIR
        if not os.path.isdir(root):
            raise LanguageError(f"no profile directory at {root}")
        profiles: List[LanguageProfile] = []
        for path in sorted(glob.glob(os.path.join(root, "*.json"))):
            with open(path, "r", encoding="utf-8") as fh:
                try:
                    raw = json.load(fh)
                except json.JSONDecodeError as exc:
                    raise LanguageError(f"{path}: {exc}") from exc
            profiles.append(_profile_from(raw, path))
        if not profiles:
            raise LanguageError(f"no profiles under {root}")
        return cls(profiles, root=root)

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._by_code)

    def __iter__(self):
        return iter(sorted(self._by_code.values(), key=lambda p: (p.kind, p.family_id)))

    def get(self, code: str) -> LanguageProfile:
        key = (code or "").lower()
        if key not in self._by_code:
            raise LanguageError(f"unknown language {code!r}")
        return self._by_code[key]

    def maybe(self, code: str) -> Optional[LanguageProfile]:
        return self._by_code.get((code or "").lower())

    def natural(self) -> List[LanguageProfile]:
        return [p for p in self if p.kind == "natural"]

    def code_languages(self) -> List[LanguageProfile]:
        return [p for p in self if p.kind == "code"]

    def for_path(self, path: str) -> Optional[LanguageProfile]:
        ext = os.path.splitext(path)[1].lower()
        return self._by_ext.get(ext)

    def for_extension(self, ext: str) -> Optional[LanguageProfile]:
        ext = ext if ext.startswith(".") else "." + ext
        return self._by_ext.get(ext.lower())

    def family_id(self, code: str) -> int:
        profile = self.maybe(code)
        return profile.family_id if profile else 0

    # ------------------------------------------------------------------
    # detection
    # ------------------------------------------------------------------
    @staticmethod
    def script_of(text: str) -> str:
        """Dominant Unicode script of the letters in `text`."""
        counts: Dict[str, int] = {}
        for ch in text[:4000]:
            if not ch.isalpha():
                continue
            cp = ord(ch)
            for script, (lo, hi) in SCRIPT_RANGES.items():
                if lo <= cp <= hi:
                    counts[script] = counts.get(script, 0) + 1
                    break
        if not counts:
            return "Zyyy"
        return max(counts.items(), key=lambda kv: kv[1])[0]

    def detect(self, text: str) -> Tuple[str, float]:
        """Best natural-language guess and a 0..1 confidence.

        Script narrows the field, then marker words break the tie (Devanagari
        covers both Hindi and Marathi, so script alone is not enough). The
        confidence is the share of marker hits, not a probability — it is
        honest about being a heuristic, and the rule set treats it as one.
        """
        if not text or len(text.strip()) < 20:
            return "en", 0.0
        script = self.script_of(text)
        candidates = [p for p in self.natural() if p.script == script]
        if not candidates:
            candidates = self.natural()
        low = " " + re.sub(r"\s+", " ", text.lower()[:8000]) + " "
        words = re.findall(r"\w+", low)
        best, best_score = candidates[0].code, 0.0
        for profile in candidates:
            if not profile.markers:
                continue
            hits = sum(1 for m in profile.markers if f" {m} " in low)
            score = hits / max(1, len(profile.markers))
            # weight by how often the marker words appear, not just presence
            density = sum(low.count(f" {m} ") for m in profile.markers) / max(1, len(words))
            score = 0.6 * score + 0.4 * min(1.0, density * 25)
            if score > best_score:
                best, best_score = profile.code, score
        if script != "Latn" and best_score == 0.0:
            return candidates[0].code, 0.5
        return best, round(min(1.0, best_score), 3)


_CACHE: Dict[str, Registry] = {}


def load_registry(root: Optional[str] = None, use_cache: bool = True) -> Registry:
    key = os.path.abspath(root or PROFILE_DIR)
    if use_cache and key in _CACHE:
        return _CACHE[key]
    registry = Registry.load(root)
    _CACHE[key] = registry
    return registry


def normalize(text: str, form: str = "NFC") -> str:
    """Unicode normalisation, as level 4 of the rule set requires."""
    return unicodedata.normalize(form, text)
