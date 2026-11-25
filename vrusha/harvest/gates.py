"""The validation gates: what may enter the store, and at which rule level.

Each gate answers one question and returns a `GateResult` carrying the rule
level that governs it, so a rejection can be traced back to `rules/levels/`.

    gate            level   question
    policy            1     is this domain permitted at all?
    secrets           1     does this text contain a key, token or password?
    licence           1     is the licence on the allow list?
    language          4     which language, and is it one we carry?
    size              6     is it long enough to be worth storing?
    quality           6     is it content, or is it navigation?
    duplicate         7     have we already got this, near enough?

The order is cheap-first. A rejected page costs the minimum possible work.
Nothing here touches the network; the caller passes in what it already has.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from ..rules import RuleSet

# --------------------------------------------------------------------------
# licence detection
# --------------------------------------------------------------------------

# Ordered most-specific-first so "LGPL" is never read as "GPL" and
# "Apache-2.0" never as an older Apache version.
LICENCE_PATTERNS: List[tuple[str, str]] = [
    ("AGPL-3.0", r"GNU\s+AFFERO\s+GENERAL\s+PUBLIC\s+LICENSE"),
    ("LGPL-3.0", r"GNU\s+LESSER\s+GENERAL\s+PUBLIC\s+LICENSE"),
    ("LGPL-2.1", r"GNU\s+LESSER\s+GENERAL\s+PUBLIC\s+LICENSE,?\s+Version\s+2\.1"),
    ("GPL-3.0", r"GNU\s+GENERAL\s+PUBLIC\s+LICENSE,?\s+Version\s+3"),
    ("GPL-2.0", r"GNU\s+GENERAL\s+PUBLIC\s+LICENSE,?\s+Version\s+2"),
    ("SSPL-1.0", r"Server\s+Side\s+Public\s+License"),
    ("BUSL-1.1", r"Business\s+Source\s+License"),
    ("Apache-2.0", r"Apache\s+License,?\s+Version\s+2\.0"),
    ("MPL-2.0", r"Mozilla\s+Public\s+License,?\s+Version\s+2\.0"),
    ("CC-BY-4.0", r"Creative\s+Commons\s+Attribution\s+4\.0"),
    ("CC0-1.0", r"CC0\s+1\.0\s+Universal"),
    ("0BSD", r"Zero-Clause\s+BSD"),
    ("BSD-3-Clause", r"Redistributions\s+in\s+binary\s+form.*?endorse"),
    ("BSD-2-Clause", r"Redistributions\s+in\s+binary\s+form"),
    ("ISC", r"Permission\s+to\s+use,\s+copy,\s+modify,\s+and(/|\s+)or\s+distribute"),
    ("MIT", r"Permission\s+is\s+hereby\s+granted,\s+free\s+of\s+charge"),
    ("Unlicense", r"This\s+is\s+free\s+and\s+unencumbered\s+software\s+released\s+into\s+the\s+public\s+domain"),
]

_SPDX = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.\-+]+)")


def detect_licence(text: str) -> Optional[str]:
    """SPDX id for a licence file's text, or None.

    None means unknown, and unknown is a refusal when the rule set says so.
    We never guess a licence from a repository's name or a README badge.
    """
    if not text:
        return None
    match = _SPDX.search(text)
    if match:
        return match.group(1).strip()
    flat = " ".join(text.split())
    for spdx, pattern in LICENCE_PATTERNS:
        if re.search(pattern, flat, re.IGNORECASE | re.DOTALL):
            return spdx
    return None


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------

def shannon_entropy(value: str) -> float:
    """Bits per character. Random-looking strings score above ~4.5."""
    if not value:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


_HIGH_ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")


def find_secrets(text: str, patterns: Sequence[str] = (), entropy_threshold: float = 4.6) -> List[str]:
    """Known key shapes plus high-entropy tokens. Returns the matches found."""
    found: List[str] = []
    for pattern in patterns:
        try:
            found.extend(m.group(0) for m in re.finditer(pattern, text))
        except re.error:
            continue
    for match in _HIGH_ENTROPY_TOKEN.finditer(text):
        token = match.group(0)
        if shannon_entropy(token) >= entropy_threshold:
            found.append(token)
    return found


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace each secret with a fixed marker. Called before anything is stored."""
    out = text
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            out = out.replace(secret, "[REDACTED]")
    return out


# --------------------------------------------------------------------------
# near-duplicate detection (minhash)
# --------------------------------------------------------------------------

def _shingles(text: str, k: int = 5) -> set:
    words = re.findall(r"\w+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def minhash_signature(text: str, num_hashes: int = 64, shingle_size: int = 5) -> List[int]:
    """A fixed-size fingerprint: the minimum hash of the shingles under each seed."""
    shingles = _shingles(text, shingle_size)
    if not shingles:
        return [0] * num_hashes
    encoded = [int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")
               for s in shingles]
    sig: List[int] = []
    for seed in range(num_hashes):
        salt = (seed * 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        sig.append(min((h ^ salt) for h in encoded))
    return sig


def signature_hex(sig: Sequence[int]) -> str:
    return "".join(f"{v:016x}" for v in sig)


def signature_from_hex(value: str) -> List[int]:
    return [int(value[i:i + 16], 16) for i in range(0, len(value), 16)]


def estimated_jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def band_keys(sig: Sequence[int], num_bands: int = 16) -> List[str]:
    """LSH banding: two texts sharing any band key are candidates for comparison."""
    if num_bands <= 0 or not sig:
        return []
    per_band = max(1, len(sig) // num_bands)
    keys = []
    for band in range(num_bands):
        chunk = sig[band * per_band:(band + 1) * per_band]
        if not chunk:
            break
        keys.append(f"{band}:" + hashlib.blake2b(
            b"".join(v.to_bytes(8, "big") for v in chunk), digest_size=8
        ).hexdigest())
    return keys


# --------------------------------------------------------------------------
# gate results
# --------------------------------------------------------------------------

@dataclass
class GateResult:
    gate: str
    level: int
    passed: bool
    action: str            # allow | block | warn
    reason: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "gate": self.gate,
            "level": self.level,
            "passed": self.passed,
            "action": self.action,
            "reason": self.reason,
            **({"detail": self.detail} if self.detail else {}),
        }


@dataclass
class GateReport:
    results: List[GateResult] = field(default_factory=list)
    redacted_text: Optional[str] = None

    @property
    def passed(self) -> bool:
        return all(r.passed or r.action != "block" for r in self.results)

    @property
    def blocked_by(self) -> List[GateResult]:
        return [r for r in self.results if not r.passed and r.action == "block"]

    @property
    def warnings(self) -> List[GateResult]:
        return [r for r in self.results if not r.passed and r.action == "warn"]

    def add(self, result: GateResult) -> None:
        self.results.append(result)

    def summary(self) -> str:
        if self.passed:
            count = len(self.warnings)
            if count:
                return f"pass ({count} warning{'s' if count != 1 else ''})"
            return "pass"
        return "; ".join(f"{r.gate}: {r.reason}" for r in self.blocked_by)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
        }


# --------------------------------------------------------------------------
# the gates
# --------------------------------------------------------------------------

def _domain_of(url: str) -> str:
    match = re.match(r"^[a-z]+://([^/]+)", url or "", re.IGNORECASE)
    host = match.group(1) if match else (url or "").split("/")[0]
    return host.lower().split(":")[0]


def gate_policy(url: str, rules: RuleSet) -> GateResult:
    """Level 1: the domain is not one we have been told to refuse."""
    refused = rules.get("policy.refused_domains", default=[]) or []
    host = _domain_of(url)
    for blocked in refused:
        if host == blocked or host.endswith("." + blocked):
            return GateResult("policy", 1, False, "block",
                              f"{host} is refused by policy.refused_domains")
    return GateResult("policy", 1, True, "allow", f"{host} permitted")


def gate_licence(licence: Optional[str], rules: RuleSet) -> GateResult:
    """Level 1: the licence is on the allow list and not on the block list."""
    allowed = [str(x).lower() for x in (rules.get("licence.allowed", default=[]) or [])]
    blocked = [str(x).lower() for x in (rules.get("licence.blocked", default=[]) or [])]
    if not licence:
        if "unknown" in blocked:
            return GateResult("licence", 1, False, "block", "no licence recorded; unknown is a refusal")
        return GateResult("licence", 1, False, "warn", "no licence recorded")
    value = licence.lower()
    if value in blocked:
        return GateResult("licence", 1, False, "block", f"{licence} is on the block list", {"licence": licence})
    if value in allowed:
        return GateResult("licence", 1, True, "allow", f"{licence} allowed", {"licence": licence})
    return GateResult("licence", 1, False, "warn", f"{licence} is not on the allow list", {"licence": licence})


def gate_secrets(text: str, rules: RuleSet) -> GateResult:
    """Level 1: no keys, tokens or private keys in the text."""
    config = rules.get("secrets.scan", default={}) or {}
    if not config.get("enabled", True):
        return GateResult("secrets", 1, True, "allow", "scan disabled")
    found = find_secrets(
        text,
        patterns=config.get("patterns", []),
        entropy_threshold=float(config.get("entropy_threshold", 4.6)),
    )
    if found:
        return GateResult("secrets", 1, False, "block",
                          f"{len(found)} secret-like string(s) found",
                          {"count": len(found), "samples": [s[:6] + "..." for s in found[:3]]})
    return GateResult("secrets", 1, True, "allow", "clean")


def gate_language(text: str, registry, rules: RuleSet) -> GateResult:
    """Level 4: which natural language, and how confident are we."""
    code, confidence = registry.detect(text)
    threshold = float(rules.get("language.detect_min_confidence", default=0.55) or 0.55)
    allowed = rules.get("language.allowed", default=[]) or []
    if allowed and code not in allowed:
        return GateResult("language", 4, False, "warn",
                          f"detected {code}, which has no profile in language.allowed",
                          {"language": code, "confidence": confidence})
    if confidence < threshold:
        return GateResult("language", 4, False, "warn",
                          f"low confidence ({confidence}) for {code}",
                          {"language": code, "confidence": confidence})
    return GateResult("language", 4, True, "allow", f"{code} ({confidence})",
                      {"language": code, "confidence": confidence})


def gate_size(text: str, rules: RuleSet) -> GateResult:
    """Level 6: long enough to be a unit of meaning."""
    minimum = int(rules.get("quality.min_words", default=120) or 120)
    words = len(re.findall(r"\w+", text))
    if words < minimum:
        return GateResult("size", 6, False, "block", f"{words} words, minimum {minimum}",
                          {"words": words})
    return GateResult("size", 6, True, "allow", f"{words} words", {"words": words})


def gate_quality(text: str, raw_chars: int, rules: RuleSet) -> GateResult:
    """Level 6: content, not navigation. Two independent measures."""
    min_ratio = float(rules.get("quality.min_text_ratio", default=0.45) or 0.45)
    max_links = float(rules.get("quality.max_link_density", default=0.30) or 0.30)
    ratio = (len(text) / raw_chars) if raw_chars else 1.0
    link_chars = sum(len(m) for m in re.findall(r"https?://\S+", text))
    link_density = (link_chars / len(text)) if text else 1.0
    problems = []
    if ratio < min_ratio:
        problems.append(f"text ratio {ratio:.2f} < {min_ratio}")
    if link_density > max_links:
        problems.append(f"link density {link_density:.2f} > {max_links}")
    detail = {"text_ratio": round(ratio, 3), "link_density": round(link_density, 3)}
    if problems:
        return GateResult("quality", 6, False, "block", "; ".join(problems), detail)
    return GateResult("quality", 6, True, "allow", "content", detail)


def gate_duplicate(text: str, index, rules: RuleSet) -> GateResult:
    """Level 7: is this the same text we already hold?

    `index` is anything with `.lookup(band_keys) -> list[str]` and
    `.signature_of(id) -> list[int]`; the store provides one. A near-duplicate
    is linked to its canonical copy, never silently dropped.
    """
    if index is None:
        return GateResult("duplicate", 7, True, "allow", "no index")
    threshold = float(rules.get("dedup.near_duplicate_threshold", default=0.97) or 0.97)
    sig = minhash_signature(text)
    candidates = index.lookup(band_keys(sig))
    for candidate_id in candidates:
        other = index.signature_of(candidate_id)
        if other and estimated_jaccard(sig, other) >= threshold:
            return GateResult("duplicate", 7, False, "block",
                              f"near-duplicate of {candidate_id}",
                              {"duplicate_of": candidate_id})
    return GateResult("duplicate", 7, True, "allow", "unique", {"bands": len(band_keys(sig))})


def evaluate(
    *,
    text: str,
    url: str,
    licence: Optional[str],
    raw_chars: int,
    rules: RuleSet,
    registry,
    index=None,
    is_code: bool = False,
) -> GateReport:
    """Run every gate that applies, cheapest first, and redact on the way out.

    Code files skip the prose gates (word count and link density mean nothing
    for a 40-line function) but keep policy, licence, secrets and duplicates.
    """
    report = GateReport()
    report.add(gate_policy(url, rules))
    if not report.passed:
        return report

    secrets_result = gate_secrets(text, rules)
    report.add(secrets_result)
    if not secrets_result.passed:
        # the text is not stored at all; do not hand back a redacted copy that
        # something downstream might save by accident
        return report

    report.add(gate_licence(licence, rules))

    if not is_code:
        report.add(gate_language(text, registry, rules))
        report.add(gate_size(text, rules))
        report.add(gate_quality(text, raw_chars, rules))

    report.add(gate_duplicate(text, index, rules))
    report.redacted_text = text
    return report
