"""Character encoding: decide it once, record how, never mangle silently.

A page's encoding can be declared in four places, and they disagree:

    1. a byte-order mark in the body        (authoritative)
    2. the HTTP `Content-Type` header       (often missing, sometimes wrong)
    3. a `<meta charset>` in the first 1 KB (often the only source on disk)
    4. nothing at all                       (guess, and say you guessed)

Guessing wrong is not cosmetic. Decoding Devanagari as UTF-8 with
`errors="replace"` turns every third byte into U+FFFD and the quality gate then
rejects the page for a reason that has nothing to do with its content. So the
result carries the encoding, where it came from, and a confidence -- and the
harvest manifest records all three, so a mis-decode is visible after the fact
rather than invisible forever.
"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from typing import Optional, Tuple

#: Byte-order marks, longest first so UTF-32 is not read as UTF-16.
_BOMS: Tuple[Tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

_META_CHARSET = re.compile(
    rb"""<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""",
    re.IGNORECASE,
)

#: Names that are really the same thing, and names browsers accept but Python
#: does not spell the same way.
_ALIASES = {
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "unicode-1-1-utf-8": "utf-8",
    "latin1": "cp1252",          # what browsers actually do with latin-1
    "iso-8859-1": "cp1252",
    "iso8859-1": "cp1252",
    "ascii": "utf-8",            # ascii is a subset; utf-8 is the safe superset
    "us-ascii": "utf-8",
    "windows-1252": "cp1252",
    "x-sjis": "shift_jis",
}


@dataclass
class CharsetResult:
    encoding: str
    source: str            # bom | header | meta | utf8 | guessed | fallback
    confidence: float      # 0..1, and it means what it says
    lossy: bool = False    # True when decoding had to replace characters

    def to_dict(self) -> dict:
        return {
            "encoding": self.encoding,
            "encoding_source": self.source,
            "encoding_confidence": round(self.confidence, 2),
            "encoding_lossy": self.lossy,
        }


def _normalise(name: str) -> Optional[str]:
    """A canonical Python codec name, or None if we cannot use it."""
    if not name:
        return None
    key = name.strip().strip("\"'").lower()
    key = _ALIASES.get(key, key)
    try:
        return codecs.lookup(key).name
    except (LookupError, ValueError):
        return None


def sniff_meta(raw: bytes, limit: int = 4096) -> Optional[str]:
    """A charset declared inside the document itself."""
    match = _META_CHARSET.search(raw[:limit])
    if not match:
        return None
    return _normalise(match.group(1).decode("ascii", errors="ignore"))


def _decodes_cleanly(raw: bytes, encoding: str, sample: int = 65536) -> bool:
    try:
        raw[:sample].decode(encoding, errors="strict")
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def detect_charset(raw: bytes, header_charset: Optional[str] = None) -> CharsetResult:
    """Pick an encoding, preferring evidence over guessing."""
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return CharsetResult(encoding, "bom", 0.99)

    declared = _normalise(header_charset or "")
    if declared and _decodes_cleanly(raw, declared):
        return CharsetResult(declared, "header", 0.90)
    # a header that cannot decode the body is a wrong header; fall through and
    # say so by the source we end up with

    meta = sniff_meta(raw)
    if meta and _decodes_cleanly(raw, meta):
        return CharsetResult(meta, "meta", 0.80)

    if _decodes_cleanly(raw, "utf-8"):
        return CharsetResult("utf-8", "utf8", 0.70)

    try:                                    # optional dependency
        from charset_normalizer import from_bytes  # type: ignore
        best = from_bytes(raw[:200_000]).best()
        if best is not None:
            encoding = _normalise(best.encoding) or "utf-8"
            return CharsetResult(encoding, "guessed", 0.55)
    except Exception:
        pass

    # Nothing worked. Decode as utf-8 and mark it lossy so the manifest shows
    # that this page's text is not trustworthy.
    return CharsetResult("utf-8", "fallback", 0.20, lossy=True)


def decode(raw: bytes, result: CharsetResult) -> Tuple[str, bool]:
    """Decode with the chosen encoding. Returns (text, lossy)."""
    try:
        return raw.decode(result.encoding, errors="strict"), False
    except (UnicodeDecodeError, LookupError):
        text = raw.decode(result.encoding, errors="replace")
        return text, True


def replacement_ratio(text: str) -> float:
    """Share of characters that are the replacement character."""
    if not text:
        return 0.0
    return text.count("\ufffd") / len(text)
