"""Chunking: one unit of meaning per row.

A chunk is the thing an address points at, the thing a vector describes and the
thing retrieval returns. Getting the boundary right matters more than the
model: a chunk holding two topics embeds to the average of two topics and
retrieves for neither.

    code   Python: ast gives real function and class boundaries.
           Everything else: brace/bracket balance, split only at depth zero.
    prose  headings define sections, blank lines define paragraphs, and a
           paragraph longer than the cap is split on sentence ends.

The `kind` of a chunk is the third segment of its address, so the vocabulary
here is fixed by `vrusha.store.address`.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List, Optional

from ..languages import LanguageProfile

CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp",
                 ".hpp", ".cc", ".go", ".rs", ".css"}


@dataclass
class Chunk:
    text: str
    kind: str                 # module|class|function|block|document|section|paragraph|claim
    symbol: str = ""          # the function or heading name, when there is one
    start_line: int = 0
    end_line: int = 0
    ordinal: int = 0
    lang: str = ""

    @property
    def chars(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "symbol": self.symbol, "start_line": self.start_line,
            "end_line": self.end_line, "ordinal": self.ordinal, "lang": self.lang,
            "chars": self.chars,
        }


# --------------------------------------------------------------------------
# code
# --------------------------------------------------------------------------

def _chunk_python(source: str, max_lines: int) -> List[Chunk]:
    """One chunk per top-level function or class; the rest is the module."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _chunk_delimited(source, max_lines)
    lines = source.splitlines()
    chunks: List[Chunk] = []
    claimed = [False] * (len(lines) + 1)

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = max(1, node.lineno)
        end = min(len(lines), getattr(node, "end_lineno", start))
        if end - start + 1 > max_lines:
            # too big to be one unit: keep the signature and split the body
            for piece in _split_lines(lines, start, end, max_lines):
                chunks.append(Chunk(
                    text="\n".join(lines[piece[0] - 1:piece[1]]),
                    kind="function" if not isinstance(node, ast.ClassDef) else "class",
                    symbol=node.name, start_line=piece[0], end_line=piece[1],
                ))
        else:
            chunks.append(Chunk(
                text="\n".join(lines[start - 1:end]),
                kind="class" if isinstance(node, ast.ClassDef) else "function",
                symbol=node.name, start_line=start, end_line=end,
            ))
        for i in range(start, end + 1):
            claimed[i] = True

    module_lines = [line for i, line in enumerate(lines, 1) if not claimed[i] and line.strip()]
    if module_lines:
        header = "\n".join(module_lines)
        if len(header) > 80:  # imports and constants are worth keeping, bare blanks are not
            chunks.append(Chunk(text=header, kind="module", symbol="", start_line=1,
                                end_line=len(lines)))
    if not chunks:
        chunks = _chunk_delimited(source, max_lines)
    chunks.sort(key=lambda c: (c.start_line, c.kind))
    return chunks


_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {v: k for k, v in _OPEN.items()}


def _chunk_delimited(source: str, max_lines: int) -> List[Chunk]:
    """Split at depth-zero blank lines. Never splits inside a group.

    Angle brackets are deliberately not counted: in TypeScript, Java and C++
    they are generics and template parameters far more often than they are
    groups, and counting them desynchronises the whole file.
    """
    lines = source.splitlines()
    chunks: List[Chunk] = []
    depth = 0
    start = 1
    buf: List[str] = []
    for index, line in enumerate(lines, 1):
        stripped = _strip_strings(line)
        for ch in stripped:
            if ch in _OPEN:
                depth += 1
            elif ch in _CLOSE:
                depth = max(0, depth - 1)
        buf.append(line)
        big = len(buf) >= max_lines
        boundary = (depth == 0 and not line.strip() and len(buf) > 3)
        if big or (boundary and len("\n".join(buf).strip()) > 60):
            text = "\n".join(buf).strip("\n")
            if text.strip():
                chunks.append(Chunk(text=text, kind=_guess_kind(text),
                                    symbol=_guess_symbol(text),
                                    start_line=start, end_line=index))
            start = index + 1
            buf = []
    tail = "\n".join(buf).strip("\n")
    if tail.strip():
        chunks.append(Chunk(text=tail, kind=_guess_kind(tail), symbol=_guess_symbol(tail),
                            start_line=start, end_line=len(lines)))
    return chunks or [Chunk(text=source, kind="module", start_line=1, end_line=len(lines))]


def _strip_strings(line: str) -> str:
    """Remove quoted spans so a brace inside a string does not count."""
    return re.sub(r'(["\'])(?:\\.|(?!\1).)*\1', '""', line)


_SYMBOL = re.compile(
    r"\b(?:def|class|function|func|fn|fun|public|private|protected|static|async|const|let|var)\s+"
    r"([A-Za-z_$][\w$]*)"
)


def _guess_symbol(text: str) -> str:
    match = _SYMBOL.search(text)
    return match.group(1) if match else ""


def _guess_kind(text: str) -> str:
    head = text[:400]
    if re.search(r"^\s*(export\s+)?(default\s+)?class\b", head, re.M) or re.search(r"\bclass\s+\w+", head):
        return "class"
    if re.search(r"\b(def|function|func|fn|fun)\s+\w+", head) or re.search(r"=>\s*\{", head):
        return "function"
    return "block"


def _split_lines(lines: List[str], start: int, end: int, max_lines: int) -> List[tuple[int, int]]:
    """Slice a long block into windows that keep the first line as context."""
    out = []
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + max_lines - 1)
        out.append((cursor, stop))
        cursor = stop + 1
    return out


# --------------------------------------------------------------------------
# prose
# --------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def _chunk_prose(text: str, max_chars: int, min_chars: int) -> List[Chunk]:
    """Sections by heading, paragraphs inside them, sentences inside those."""
    lines = text.splitlines()
    sections: List[tuple[str, List[str], int]] = []   # (title, lines, start_line)
    title = ""
    buf: List[str] = []
    start = 1
    for index, line in enumerate(lines, 1):
        heading = _HEADING.match(line.strip())
        if heading:
            if any(l.strip() for l in buf):
                sections.append((title, buf, start))
            title = heading.group(2).strip()
            buf = []
            start = index
            continue
        buf.append(line)
    if any(l.strip() for l in buf):
        sections.append((title, buf, start))
    if not sections:
        sections = [("", lines, 1)]

    chunks: List[Chunk] = []
    for section_title, section_lines, section_start in sections:
        body = "\n".join(section_lines).strip()
        if not body:
            continue
        if len(body) <= max_chars:
            if len(body) >= min_chars:
                chunks.append(Chunk(text=body, kind="section" if section_title else "paragraph",
                                    symbol=section_title, start_line=section_start,
                                    end_line=section_start + len(section_lines) - 1))
            continue
        # split the section into paragraphs, then merge small ones back together
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        current: List[str] = []
        size = 0
        for para in paragraphs:
            if len(para) > max_chars:
                if current:
                    chunks.append(Chunk(text="\n\n".join(current), kind="section",
                                        symbol=section_title, start_line=section_start))
                    current, size = [], 0
                for piece in _split_sentences(para, max_chars):
                    if len(piece) >= min_chars:
                        chunks.append(Chunk(text=piece, kind="paragraph",
                                            symbol=section_title, start_line=section_start))
                continue
            if size + len(para) > max_chars and current:
                chunks.append(Chunk(text="\n\n".join(current), kind="section",
                                    symbol=section_title, start_line=section_start))
                current, size = [], 0
            current.append(para)
            size += len(para) + 2
        if current and size >= min_chars:
            chunks.append(Chunk(text="\n\n".join(current), kind="section",
                                symbol=section_title, start_line=section_start))
    return [c for c in chunks if c.chars >= min_chars] or (
        [Chunk(text=text.strip(), kind="document", start_line=1, end_line=len(lines))]
        if text.strip() else []
    )


def _split_sentences(text: str, max_chars: int) -> List[str]:
    parts = re.split(r"(?<=[.!?।])\s+", text)
    out: List[str] = []
    buf = ""
    for part in parts:
        if len(buf) + len(part) + 1 > max_chars and buf:
            out.append(buf.strip())
            buf = part
        else:
            buf = f"{buf} {part}".strip()
    if buf:
        out.append(buf.strip())
    return out


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def chunk_text(text: str, profile: Optional[LanguageProfile], cfg: dict,
               url: str = "") -> List[Chunk]:
    """Split one document into chunks, using the language profile's strategy."""
    store_cfg = cfg.get("store", {})
    max_lines = int(store_cfg.get("max_chunk_lines", 60))
    max_chars = int(store_cfg.get("max_chunk_chars", 4000))
    min_chars = int(store_cfg.get("min_chunk_chars", 80))

    if not text or not text.strip():
        return []

    strategy = profile.chunk_strategy if profile else "paragraph"
    lang = profile.code if profile else ""

    if strategy == "ast":
        chunks = _chunk_python(text, max_lines)
    elif strategy in ("delimiter", "line"):
        chunks = _chunk_delimited(text, max_lines)
    else:
        chunks = _chunk_prose(text, max_chars, min_chars)

    for index, chunk in enumerate(chunks):
        chunk.ordinal = index
        chunk.lang = lang
    return chunks
