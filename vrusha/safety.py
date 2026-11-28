"""Hostile input: a page that tries to give the agent orders.

Every other defence in this project assumes the input is *wrong*. This one assumes
it is **aimed**. A harvested page, a blackboard entry, a table cell — anything that
reaches a prompt can carry text written to be obeyed: *"ignore previous
instructions"*, *"you are now in developer mode"*, *"send the store to this URL"*.

The design rule, from `docs/AGENTS.md` §7: **content is data, never instructions.**
This module is the part that makes that checkable:

- `scan()` finds instruction-shaped spans and says what kind and how bad;
- `neutralise()` replaces them with a marker, keeping the surrounding text;
- `is_hostile()` is the one-line gate for a caller that only needs a yes.

It is **not** a guarantee. A determined attacker writes prose that no pattern sees,
and a pattern that catches everything also catches documentation — this repository
is full of sentences about prompts, and they must not be flagged. So the patterns
are narrow, the severities are labelled, and the work log records what slips past.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence

#: (label, pattern, severity). Severity 3 is "this is an attempt", 1 is "looks like
#: documentation *about* an attempt" — which this repository is full of, so the
#: low band is reported and not acted on.
PATTERNS: Sequence[tuple] = (
    ("override", r"\b(ignore|disregard|forget)\b[^.\n]{0,30}\b(previous|prior|above|earlier)\b[^.\n]{0,20}\b(instruction|prompt|rule|direction)s?\b", 3),
    ("role_change", r"\byou are (now|no longer)\b[^.\n]{0,40}", 3),
    ("developer_mode", r"\b(developer|debug|god|admin|dan)\s+mode\b", 3),
    ("system_tag", r"(^|\n)\s*(system|assistant|user)\s*:", 2),
    ("new_instructions", r"\b(new|updated|real)\s+instructions?\s*:", 3),
    ("exfiltration", r"\b(send|post|upload|email|exfiltrate)\b[^.\n]{0,40}\b(to|at)\b\s*(https?://|[\w.-]+@)", 3),
    ("credential_request", r"\b(reveal|print|show|output)\b[^.\n]{0,30}\b(system prompt|api key|token|password|secret)s?\b", 3),
    ("tool_hijack", r"\bcall\b[^.\n]{0,20}\b(tool|function|command)\b[^.\n]{0,30}\b(with|using)\b", 2),
    ("delimiter_break", r"(<\|im_start\|>|<\|im_end\|>|\[/?INST\]|<<SYS>>|</?system>)", 3),
)

_COMPILED = tuple((label, re.compile(pattern, re.IGNORECASE | re.MULTILINE),
                   severity) for label, pattern, severity in PATTERNS)

#: Severity at or above this is an attempt, not a mention.
HOSTILE = 3

#: Words that mark a *mention* rather than an attempt: this repository documents
#: these patterns, so `ignore previous instructions` appears in its own prose. A
#: scanner that flags its own documentation is a scanner nobody keeps running.
MENTION = re.compile(
    r"\b(how to|explains?|documents?|describes?|example|detects?|flags?|pattern|"
    r"test|quoted|the phrase|such as|quarantin\w*|aimed at the reader)\b",
    re.IGNORECASE)

#: How far back to look for a mention marker.
MENTION_WINDOW = 60


@dataclass
class Finding:
    kind: str
    severity: int
    start: int
    end: int
    excerpt: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "severity": self.severity,
                "start": self.start, "end": self.end, "excerpt": self.excerpt}


#: A cheap gate before the regexes. Every pattern needs one of these words, so a
#: page without any of them cannot match -- and most pages have none. Measured:
#: without the gate, scanning 11 KB cost **4.8 ms**, a 50% tax on a 9 ms query.
GATE = ("ignore", "disregard", "forget", "you are", "mode", "instruction",
        "system", "assistant", "user:", "send", "post", "upload", "email",
        "reveal", "print", "show", "output", "call", "tool", "function",
        "<|", "[inst]", "<<sys>>", "<system")


def scan(text: str, *, limit: int = 50) -> List[Finding]:
    """Every instruction-shaped span, highest severity first."""
    if not text:
        return []
    lowered = text.lower()
    if not any(word in lowered for word in GATE):
        return []
    found: List[Finding] = []
    for label, pattern, severity in _COMPILED:
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            # a phrase quoted inside a sentence about explaining is a mention --
            # and the explanation often comes AFTER the quote, as in this
            # repository's own blackboard example, so both sides are checked
            context = text[max(0, start - MENTION_WINDOW):end + MENTION_WINDOW]
            if severity >= HOSTILE and MENTION.search(context):
                severity_here = 2
            else:
                severity_here = severity
            found.append(Finding(kind=label, severity=severity_here, start=start,
                                 end=end,
                                 excerpt=text[start:min(end, start + 120)]))
            if len(found) >= limit:
                break
    found.sort(key=lambda item: (-item.severity, item.start))
    return found


def is_hostile(text: str) -> bool:
    """True when anything in the text is an *attempt*, not a mention."""
    return any(finding.severity >= HOSTILE for finding in scan(text, limit=5))


def neutralise(text: str, *, marker: str = "[removed: instruction-shaped text]",
               floor: int = HOSTILE) -> str:
    """Replace hostile spans, keeping everything around them.

    A span is removed, not the whole document: a page with one injected line still
    has a body worth reading, and dropping it would be the attacker deciding what
    the corpus contains.
    """
    if not text:
        return ""
    spans = [finding for finding in scan(text) if finding.severity >= floor]
    if not spans:
        return text
    pieces, cursor = [], 0
    for finding in sorted(spans, key=lambda item: item.start):
        if finding.start < cursor:
            continue                     # overlapping match: the first one wins
        pieces.append(text[cursor:finding.start])
        pieces.append(marker)
        cursor = finding.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def summary(findings: Sequence[Finding]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return counts
