"""The ten-level rule resolver.

Every rule in the system is filed under one of ten levels. Level 1 is the
strictest; level 10 is the weakest. When two rules speak to the same key, the
lower level number wins, always. There is no "but" and no override file: if you
want different behaviour at level 1 you edit level 1.

    level 01  inviolable   licence, robots.txt, secrets, personal data, safety
    level 02  legal        attribution, copyright, terms of use
    level 03  integrity    provenance, no fabrication, timestamps
    level 04  language     encoding, script, language confidence
    level 05  structure    address scheme, chunk boundaries, schema version
    level 06  quality      minimum length, text ratio, boilerplate
    level 07  dedup        similarity thresholds, merge policy
    level 08  retrieval    top-k, fusion weights, refusal threshold
    level 09  training     split, token budget, sequence length
    level 10  preference   defaults and style you may change freely

A rule is a JSON object:

    {
      "id":      "licence.allowed",         # unique across the whole set
      "key":     "licence.allowed",         # the question being answered
      "level":   1,                         # optional, defaults to the file's level
      "action":  "allow",                   # allow | block | warn | set | prefer
      "value":   ["MIT", "Apache-2.0"],     # payload, any JSON
      "scope":   ["harvest", "code"],       # optional; empty means everywhere
      "rationale": "why this rule exists"   # required, so the trace can explain itself
    }

`RuleSet.decide(key, scope)` returns the winning rule and the full trace of the
rules it beat, which is what `vrusha rules --explain <key>` prints.
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

LEVELS: Dict[int, str] = {
    1: "inviolable",
    2: "legal",
    3: "integrity",
    4: "language",
    5: "structure",
    6: "quality",
    7: "dedup",
    8: "retrieval",
    9: "training",
    10: "preference",
}

ACTIONS = {"allow", "block", "warn", "set", "prefer"}

DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "levels")


class RuleError(ValueError):
    """A rule file is malformed, or two rules claim the same id."""


@dataclass(frozen=True)
class Rule:
    id: str
    key: str
    level: int
    action: str
    value: Any = None
    scope: tuple[str, ...] = ()
    rationale: str = ""
    source: str = ""

    def applies_to(self, scope: Optional[str]) -> bool:
        if not self.scope:
            return True
        return scope is not None and scope in self.scope

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "level": self.level,
            "level_name": LEVELS.get(self.level, "?"),
            "action": self.action,
            "value": self.value,
            "scope": list(self.scope),
            "rationale": self.rationale,
            "source": self.source,
        }


@dataclass
class Decision:
    """The answer to `decide(key)`, with the reasoning attached."""

    key: str
    rule: Optional[Rule]
    trace: List[Rule] = field(default_factory=list)

    @property
    def action(self) -> str:
        return self.rule.action if self.rule else "allow"

    @property
    def value(self) -> Any:
        return self.rule.value if self.rule else None

    @property
    def level(self) -> Optional[int]:
        return self.rule.level if self.rule else None

    @property
    def blocked(self) -> bool:
        return self.action == "block"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "winner": self.rule.to_dict() if self.rule else None,
            "trace": [r.to_dict() for r in self.trace],
        }


class RuleSet:
    """Every rule, indexed by key, ordered by level."""

    def __init__(self, rules: Iterable[Rule], root: str = ""):
        self.root = root
        self._rules: List[Rule] = sorted(
            rules, key=lambda r: (r.level, r.id)
        )
        self._by_key: Dict[str, List[Rule]] = {}
        seen: Dict[str, Rule] = {}
        for rule in self._rules:
            if rule.id in seen:
                raise RuleError(
                    f"duplicate rule id {rule.id!r} in {rule.source} and "
                    f"{seen[rule.id].source}"
                )
            seen[rule.id] = rule
            self._by_key.setdefault(rule.key, []).append(rule)

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, root: str | None = None) -> "RuleSet":
        root = root or DEFAULT_ROOT
        if not os.path.isdir(root):
            raise RuleError(f"no rule directory at {root}")
        rules: List[Rule] = []
        pattern = os.path.join(root, "level-*", "*.json")
        for path in sorted(glob.glob(pattern)):
            rules.extend(cls._read_file(path))
        if not rules:
            raise RuleError(f"no rules found under {root}")
        return cls(rules, root=root)

    @staticmethod
    def _read_file(path: str) -> List[Rule]:
        with open(path, "r", encoding="utf-8") as fh:
            try:
                doc = json.load(fh)
            except json.JSONDecodeError as exc:
                raise RuleError(f"{path}: {exc}") from exc
        if not isinstance(doc, dict) or "rules" not in doc:
            raise RuleError(f"{path}: expected an object with a 'rules' list")
        file_level = int(doc.get("level", 0))
        folder = os.path.basename(os.path.dirname(path))
        if folder.startswith("level-"):
            folder_level = int(folder.split("-", 1)[1])
            if file_level and file_level != folder_level:
                raise RuleError(
                    f"{path}: file says level {file_level} but sits in {folder}"
                )
            file_level = folder_level
        if file_level not in LEVELS:
            raise RuleError(f"{path}: level {file_level} is not 1..10")

        out: List[Rule] = []
        for raw in doc["rules"]:
            rid = str(raw.get("id") or "").strip()
            if not rid:
                raise RuleError(f"{path}: a rule has no id")
            action = str(raw.get("action", "set")).strip().lower()
            if action not in ACTIONS:
                raise RuleError(f"{path}: rule {rid} has action {action!r}")
            level = int(raw.get("level", file_level))
            if level not in LEVELS:
                raise RuleError(f"{path}: rule {rid} has level {level}")
            scope = raw.get("scope") or []
            if isinstance(scope, str):
                scope = [scope]
            out.append(
                Rule(
                    id=rid,
                    key=str(raw.get("key") or rid),
                    level=level,
                    action=action,
                    value=raw.get("value"),
                    scope=tuple(str(s) for s in scope),
                    rationale=str(raw.get("rationale", "")),
                    source=os.path.relpath(path, os.path.dirname(DEFAULT_ROOT)),
                )
            )
        return out

    # ------------------------------------------------------------------
    # querying
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._rules)

    def __iter__(self):
        return iter(self._rules)

    def keys(self) -> List[str]:
        return sorted(self._by_key)

    def for_key(self, key: str, scope: Optional[str] = None) -> List[Rule]:
        return [r for r in self._by_key.get(key, []) if r.applies_to(scope)]

    def decide(self, key: str, scope: Optional[str] = None, default: Any = None) -> Decision:
        """The lowest-numbered level that speaks to `key` wins."""
        candidates = self.for_key(key, scope)
        if not candidates:
            return Decision(key=key, rule=None, trace=[])
        return Decision(key=key, rule=candidates[0], trace=candidates)

    def get(self, key: str, scope: Optional[str] = None, default: Any = None) -> Any:
        """Convenience: the winning value, or `default` when no rule speaks."""
        decision = self.decide(key, scope)
        return default if decision.rule is None else decision.value

    def level_counts(self) -> Dict[int, int]:
        counts = {level: 0 for level in LEVELS}
        for rule in self._rules:
            counts[rule.level] += 1
        return counts

    def conflicts(self) -> List[tuple[str, List[Rule]]]:
        """Keys where more than one level speaks — worth reviewing by hand."""
        out = []
        for key in self.keys():
            rules = self._by_key[key]
            if len({r.level for r in rules}) > 1:
                out.append((key, rules))
        return out

    def check(self) -> List[str]:
        """Structural problems. Empty list means the set is sound."""
        problems: List[str] = []
        for rule in self._rules:
            if not rule.rationale:
                problems.append(f"{rule.id}: no rationale")
            if rule.level == 1 and rule.action in {"prefer", "set"}:
                problems.append(
                    f"{rule.id}: level 1 should block/allow/warn, not {rule.action}"
                )
        for level in LEVELS:
            if not any(r.level == level for r in self._rules):
                problems.append(f"level {level} ({LEVELS[level]}) has no rules")
        return problems


_CACHE: Dict[str, RuleSet] = {}


def load_rules(root: str | None = None, use_cache: bool = True) -> RuleSet:
    """Load and memoise the rule set for a root directory."""
    key = os.path.abspath(root or DEFAULT_ROOT)
    if use_cache and key in _CACHE:
        return _CACHE[key]
    rules = RuleSet.load(root)
    _CACHE[key] = rules
    return rules
