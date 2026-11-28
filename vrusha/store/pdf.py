"""PDF: text with coordinates, then a grid, then a table — or nothing.

A PDF has no markup. There is no `<td>`, no header row, no column type. There is
a page of glyphs at coordinates, and whether they form a table is an inference
you make and can get wrong. So this module is deliberately two stages:

    extract_text_items()   bytes -> (x, y, size, text) on a page
    tables_from_items()    items -> rows and columns, or a refusal

The second stage is allowed to refuse. A PDF table reconstructed from
whitespace can be confidently wrong in a way an HTML table cannot: nothing in
the file says the third column is a price, and a wrong column boundary turns
one number into two. When the grid is not consistent, this returns nothing and
says why, and the document still goes into the store as text.

What this reads: uncompressed and FlateDecode content streams, the `Tj`, `TJ`,
`'` and `"` operators, `Td`/`TD`/`Tm` positioning, and literal strings with
PDF escapes. What it does not read: encryption, Type0/CID fonts with custom
encodings, text drawn as vector outlines, rotated pages, or anything where the
glyphs carry no simple mapping to characters. Those return no items rather than
nonsense, because a table of mojibake is worse than no table.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .extract import Table

_OBJECT = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.DOTALL)
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)

#: Operators that move the text cursor or draw text. Everything else in a
#: content stream (paths, colours, images) is skipped by the tokeniser.
_TEXT_OPERATORS = {b"Tj", b"TJ", b"'", b'"', b"Td", b"TD", b"Tm", b"T*", b"BT", b"ET", b"Tf"}

_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
            b"(": b"(", b")": b")", b"\\": b"\\"}


@dataclass
class Item:
    """One run of text at a position on a page."""

    x: float
    y: float
    size: float
    text: str
    page: int = 0


@dataclass
class Grid:
    """A candidate table: rows of cells, with the evidence that made it one."""

    rows: List[List[str]] = field(default_factory=list)
    columns: List[float] = field(default_factory=list)
    fill: float = 0.0            # share of grid positions that had text
    reason: str = ""


# ---------------------------------------------------------------------------
# the file: objects, streams, decompression
# ---------------------------------------------------------------------------


def _objects(data: bytes) -> List[Tuple[bytes, bytes]]:
    """(dictionary, stream bytes) for every object in the file."""
    output: List[Tuple[bytes, bytes]] = []
    for match in _OBJECT.finditer(data):
        body = match.group(3)
        stream = _STREAM.search(body)
        output.append((body, stream.group(1) if stream else b""))
    return output


def _decode(dictionary: bytes, raw: bytes) -> bytes:
    """Inflate a stream when the dictionary says it is deflated.

    A stream that will not inflate is dropped: a half-decoded content stream
    produces text at wrong coordinates, which is exactly the confident nonsense
    this module refuses to emit.
    """
    if b"/FlateDecode" in dictionary:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompressobj().decompress(raw)
            except zlib.error:
                return b""
    if b"/Filter" in dictionary:
        return b""                      # an encoding we do not implement
    return raw


# ---------------------------------------------------------------------------
# the content stream: a very small tokeniser
# ---------------------------------------------------------------------------


def _literal_string(data: bytes, index: int) -> Tuple[bytes, int]:
    """Read a `(...)` string with PDF escapes, handling nested parentheses."""
    depth = 0
    out = bytearray()
    while index < len(data):
        char = data[index:index + 1]
        if char == b"\\":
            nxt = data[index + 1:index + 2]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                index += 2
                continue
            if nxt.isdigit():                     # \ddd octal
                digits = data[index + 1:index + 4]
                octal = b""
                for digit in digits:
                    if bytes([digit]).isdigit() and len(octal) < 3:
                        octal += bytes([digit])
                    else:
                        break
                try:
                    out.append(int(octal, 8) & 0xFF)
                except ValueError:
                    pass
                index += 1 + len(octal)
                continue
            if nxt in (b"\n", b"\r"):             # line continuation
                index += 2
                continue
            out += nxt
            index += 2
            continue
        if char == b"(":
            depth += 1
            if depth > 1:
                out += char
            index += 1
            continue
        if char == b")":
            depth -= 1
            if depth == 0:
                return bytes(out), index + 1
            out += char
            index += 1
            continue
        out += char
        index += 1
    return bytes(out), index


def _tokens(data: bytes) -> Iterable[Tuple[str, object]]:
    """Operands and operators, in order. Anything unrecognised is skipped."""
    index = 0
    length = len(data)
    while index < length:
        char = data[index:index + 1]
        if char in b" \t\r\n\x00\f":
            index += 1
            continue
        if char == b"%":
            while index < length and data[index:index + 1] not in b"\r\n":
                index += 1
            continue
        if char == b"(":
            text, index = _literal_string(data, index)
            yield ("string", text)
            continue
        if char == b"<" and data[index:index + 2] != b"<<":
            end = data.find(b">", index)
            if end < 0:
                return
            hex_text = re.sub(rb"[^0-9A-Fa-f]", b"", data[index + 1:end])
            if len(hex_text) % 2:
                hex_text += b"0"
            try:
                yield ("string", bytes.fromhex(hex_text.decode("ascii")))
            except ValueError:
                pass
            index = end + 1
            continue
        if char == b"[":
            # an array: read its operands, then the operator that follows
            depth = 1
            start = index + 1
            cursor = start
            while cursor < length and depth:
                current = data[cursor:cursor + 1]
                if current == b"(":
                    _, cursor = _literal_string(data, cursor)
                    continue
                if current == b"[":
                    depth += 1
                elif current == b"]":
                    depth -= 1
                cursor += 1
            for token in _tokens(data[start:cursor - 1]):
                yield token
            yield ("array_end", None)
            index = cursor
            continue
        if char == b"/":
            end = index + 1
            while end < length and data[end:end + 1] not in b" \t\r\n\x00\f()<>[]{}/%":
                end += 1
            yield ("name", data[index + 1:end])
            index = end
            continue
        if char in b"+-.0123456789":
            end = index
            while end < length and data[end:end + 1] in b"+-.0123456789eE":
                end += 1
            try:
                yield ("number", float(data[index:end]))
            except ValueError:
                pass
            index = end
            continue
        end = index
        while end < length and data[end:end + 1] not in b" \t\r\n\x00\f()<>[]{}/%":
            end += 1
        word = data[index:end]
        if word:
            yield ("operator", word)
        index = max(end, index + 1)


def _text_of(raw: bytes) -> str:
    """A PDF string is bytes. Latin-1 keeps every byte, and UTF-16BE is flagged."""
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw[2:].decode("utf-16-be", errors="replace")
        except Exception:                       # noqa: BLE001
            return ""
    return raw.decode("latin-1", errors="replace")


def items_from_stream(stream: bytes, page: int = 0) -> List[Item]:
    """Every text run in one content stream, with its position."""
    items: List[Item] = []
    operands: List[Tuple[str, object]] = []
    x = y = 0.0
    line_x = line_y = 0.0
    size = 10.0
    in_text = False

    for kind, value in _tokens(stream):
        if kind == "array_end":
            continue
        if kind != "operator":
            operands.append((kind, value))
            continue

        operator = value if isinstance(value, bytes) else b""
        if operator == b"BT":
            in_text, x, y, line_x, line_y = True, 0.0, 0.0, 0.0, 0.0
        elif operator == b"ET":
            in_text = False
        elif operator == b"Tf":
            numbers = [v for k, v in operands if k == "number"]
            if numbers:
                size = float(numbers[-1]) or 10.0
        elif operator in (b"Td", b"TD"):
            numbers = [v for k, v in operands if k == "number"]
            if len(numbers) >= 2:
                line_x += float(numbers[-2])
                line_y += float(numbers[-1])
                x, y = line_x, line_y
        elif operator == b"Tm":
            numbers = [v for k, v in operands if k == "number"]
            if len(numbers) >= 6:
                line_x, line_y = float(numbers[-2]), float(numbers[-1])
                x, y = line_x, line_y
        elif operator == b"T*":
            line_y -= size * 1.2
            x, y = line_x, line_y
        elif operator in (b"Tj", b"'", b'"'):
            if operator in (b"'", b'"'):
                line_y -= size * 1.2
                x, y = line_x, line_y
            strings = [v for k, v in operands if k == "string"]
            if strings:
                text = _text_of(strings[-1]).strip()
                if text:
                    items.append(Item(x=x, y=y, size=size, text=text, page=page))
        elif operator == b"TJ":
            pieces: List[str] = []
            for kind_, value_ in operands:
                if kind_ == "string":
                    pieces.append(_text_of(value_))
                elif kind_ == "number" and value_ < -100:
                    pieces.append(" ")        # a wide kern is a word gap
            text = "".join(pieces).strip()
            if text:
                items.append(Item(x=x, y=y, size=size, text=text, page=page))
        operands = []
    return items


def extract_text_items(data: bytes) -> List[Item]:
    """All the text in a PDF, page by page, with coordinates."""
    items: List[Item] = []
    page = 0
    for dictionary, raw in _objects(data):
        if not raw:
            continue
        stream = _decode(dictionary, raw)
        if not stream:
            continue
        if b"BT" not in stream and b"Tj" not in stream and b"TJ" not in stream:
            continue                        # not a content stream
        page += 1
        items.extend(items_from_stream(stream, page=page))
    return items


# ---------------------------------------------------------------------------
# items -> a grid, or a refusal
# ---------------------------------------------------------------------------


def _cluster(values: Sequence[float], tolerance: float) -> List[float]:
    """Group nearby coordinates, returning the mean of each group."""
    if not values:
        return []
    ordered = sorted(values)
    groups: List[List[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def grid_from_items(items: Sequence[Item], *, row_tolerance: float = 3.0,
                    column_tolerance: float = 12.0) -> Grid:
    """Reconstruct a table from positioned text, or explain why it is not one.

    Rows are shared y values; columns are x positions that recur. The fill
    ratio — how much of the resulting grid actually had text — is the
    confidence: a real table is nearly full, and a page of prose that happens
    to align is not.
    """
    if len(items) < 4:
        return Grid(reason=f"{len(items)} text run(s): too few for a table")

    rows_y = _cluster([item.y for item in items], row_tolerance)
    # a PDF's y axis points up, so the first row on the page is the largest y:
    # reverse the clusters or every table comes out upside down, header last
    rows_y = sorted(rows_y, reverse=True)
    columns_x = _cluster([item.x for item in items], column_tolerance)
    if len(rows_y) < 2:
        return Grid(reason=f"{len(rows_y)} row(s): a table needs two")
    if len(columns_x) < 2:
        return Grid(reason=f"{len(columns_x)} column(s): a table needs two")
    if len(columns_x) > 20:
        return Grid(reason=f"{len(columns_x)} columns: this is prose, not a table")

    rows: Dict[int, Dict[int, List[str]]] = {}
    for item in items:
        row_index = min(range(len(rows_y)), key=lambda i: abs(rows_y[i] - item.y))
        column_index = min(range(len(columns_x)),
                           key=lambda i: abs(columns_x[i] - item.x))
        rows.setdefault(row_index, {}).setdefault(column_index, []).append(item.text)

    filled = sum(len(cells) for cells in rows.values())
    grid = [[(" ".join(rows[r][c]) if r in rows and c in rows[r] else "")
             for c in range(len(columns_x))] for r in range(len(rows_y))]
    fill = filled / max(1, len(rows_y) * len(columns_x))

    if fill < 0.6:
        return Grid(rows=grid, columns=columns_x, fill=fill,
                    reason=f"only {fill:.0%} of the grid has text")
    return Grid(rows=grid, columns=columns_x, fill=fill,
                reason=f"{len(rows_y)} rows, {len(columns_x)} columns, {fill:.0%} full")


def tables_from_pdf(data: bytes, source: str = "", *,
                    row_tolerance: float = 3.0,
                    column_tolerance: float = 12.0) -> List[Table]:
    """Every grid in the PDF that is full enough to be a table.

    A PDF page often holds one table and a paragraph of prose. Both become
    items; only the consistent grid becomes a `Table`, and the prose stays in
    the document text where retrieval can still find it.
    """
    items = extract_text_items(data)
    if not items:
        return []

    # split into vertical bands: a large gap in y separates a table from prose
    ordered = sorted(items, key=lambda item: (-item.y, item.x))
    bands: List[List[Item]] = []
    for item in ordered:
        if bands and abs(bands[-1][-1].y - item.y) <= max(30.0, item.size * 3):
            bands[-1].append(item)
        else:
            bands.append([item])

    tables: List[Table] = []
    for band in bands:
        grid = grid_from_items(band, row_tolerance=row_tolerance,
                               column_tolerance=column_tolerance)
        if not grid.rows or grid.fill < 0.6:
            continue
        table = Table(rows=grid.rows,
                      header_flags=[True] + [False] * (len(grid.rows) - 1),
                      source=source)
        tables.append(table)
    return tables
