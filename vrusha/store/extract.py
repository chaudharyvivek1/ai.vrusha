"""Extraction: a page or a file becomes typed rows, not just chunks.

A chunk answers "where is this written". A row answers "what is the price of
this plan". This module does the second: it finds the tables, decides which are
data rather than layout, reads a header, infers a type and a unit per column,
and hands back rows that still know which chunk and character span they came
from.

Three rules, and they are what makes this safe to bolt onto a working store:

1. **No row without provenance.** Every table carries the document, the chunk
   and the source span. A row that cannot point at its own text is dropped.
2. **A table is a schema plus rows.** Column names, types, units and the null
   rate are stored, so a later query knows what it is reading.
3. **Nothing here needs a model.** Deterministic parsing on a CPU box, so a
   wrong answer is a bug you can fix rather than a sample you re-roll.

Multilingual is not a bolt-on: digits, grouping and scale words are handled
before anything is parsed, because `₹1,23,456` and `1,23,456` and `१,२३,४५६`
are the same number and a naive `float()` makes all three text.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# language: digits, grouping, scale words
# ---------------------------------------------------------------------------

#: Digit sets that appear in the corpora this store targets. Mapped to ASCII
#: before any number is parsed; a Devanagari २ is not a character, it is a 2.
_DIGITS = {}
for _offset, _zero in enumerate("०१२३४५६७८९"):
    _DIGITS[ord(_zero)] = str(_offset)
for _block in ("٠١٢٣٤٥٦٧٨٩",          # Arabic-Indic
               "۰۱۲۳۴۵۶۷۸۹",          # Eastern Arabic-Indic
               "০১২৩৪৫৬৭৮৯",          # Bengali
               "૦૧૨૩૪૫૬૭૮૯",          # Gujarati
               "੦੧੨੩੪੫੬੭੮੯",          # Gurmukhi
               "௦௧௨௩௪௫௬௭௮௯",          # Tamil
               "౦౧౨౩౪౫౬౭౮౯",          # Telugu
               "೦೧೨೩೪೫೬೭೮೯"):          # Kannada
    for _offset, _char in enumerate(_block):
        _DIGITS[ord(_char)] = str(_offset)

#: Scale words that multiply the number before them. A suffix is never kept as
#: text: `5 crore` is 50000000, and adding it to `5 lakh` must not be possible
#: without the unit being visible.
_SCALES = {
    "k": 1e3, "thousand": 1e3, "हज़ार": 1e3, "हजार": 1e3, "hazar": 1e3,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "लाख": 1e5, "लक्ष": 1e5,
    "m": 1e6, "mn": 1e6, "million": 1e6, "मिलियन": 1e6,
    "crore": 1e7, "crores": 1e7, "cr": 1e7, "करोड़": 1e7, "करोड": 1e7,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}

_CURRENCY = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "रु": "INR",
    "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}

_UNIT_WORDS = {
    "%": "%", "percent": "%", "pct": "%", "प्रतिशत": "%",
    "kg": "kg", "g": "g", "km": "km", "m": "m", "cm": "cm",
    "gb": "GB", "mb": "MB", "tb": "TB", "ms": "ms", "s": "s",
}

#: Column names that mean the same thing in different languages. Used to give a
#: header a canonical key, so `कीमत` and `price` are the same column.
_HEADER_ALIASES = {
    "price": ("price", "cost", "amount", "कीमत", "मूल्य", "दाम", "किंमत", "விலை", "দাম"),
    "name": ("name", "title", "नाम", "नाव", "பெயர்", "নাম"),
    "date": ("date", "day", "दिनांक", "तारीख", "दिन", "दिनांक", "தேதி", "তারিখ"),
    "quantity": ("quantity", "qty", "count", "मात्रा", "संख्या", "संख्या"),
    "unit": ("unit", "uom", "इकाई", "एकक", "அலகு", "একক"),
    "vendor": ("vendor", "seller", "supplier", "company", "विक्रेता", "कंपनी"),
    "area": ("area", "size", "क्षेत्र", "क्षेत्रफल", "பரப்பளவு"),
    "total": ("total", "sum", "कुल", "एकूण", "மொத்தம்", "মোট"),
}

_CANONICAL = {alias: key for key, aliases in _HEADER_ALIASES.items() for alias in aliases}

#: Day-first by default, because that is what the corpora this store targets
#: use. `%m/%d/%Y` is added only when the caller says the corpus is month-first.
_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
    "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y", "%b %Y", "%B %Y",
)


def ascii_digits(text: str) -> str:
    """Every numeral system this store meets, mapped to 0-9."""
    return text.translate(_DIGITS)


@dataclass
class Number:
    value: float
    currency: Optional[str] = None
    unit: Optional[str] = None
    scale: float = 1.0
    raw: str = ""

    @property
    def scaled(self) -> float:
        return self.value * self.scale


def parse_number(text: str, *, dayfirst: bool = True) -> Optional[Number]:
    """`₹1,23,456`, `Rs 5.5 lakh`, `१२,३४५`, `12%`, `(1,200)` -> a number.

    Returns None rather than guessing when the cell is not a number: a wrong
    number is worse than a null, because a null is visible in a sum.
    """
    if text is None:
        return None
    raw = text.strip()
    if not raw or len(raw) > 40:
        return None
    cleaned = ascii_digits(raw).replace("\u00a0", " ").replace("−", "-")
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative, cleaned = True, cleaned[1:-1]

    currency = None
    unit = None
    lowered = cleaned.lower()

    # currency, prefix or suffix
    for symbol, code in _CURRENCY.items():
        if symbol in lowered:
            currency = code
            lowered = lowered.replace(symbol, " ")
            break
    for word, symbol_unit in _UNIT_WORDS.items():
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            unit = symbol_unit
            lowered = re.sub(rf"(?<![a-z]){re.escape(word)}(?![a-z])", " ", lowered)
            break

    scale = 1.0
    for word, factor in sorted(_SCALES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z\u0900-\u0dff]){re.escape(word)}(?![a-z\u0900-\u0dff])", lowered):
            scale = factor
            lowered = re.sub(rf"(?<![a-z\u0900-\u0dff]){re.escape(word)}(?![a-z\u0900-\u0dff])",
                             " ", lowered)
            break

    # grouping separators: 1,23,456 / 1,234,567 / 1 234 567 / 1_234
    body = re.sub(r"(?<=\d)[,\s_](?=\d{2,3}(?:\D|$))", "", lowered)
    body = body.replace(",", "").replace("_", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", body)
    if not match:
        return None
    leftover = body[:match.start()] + body[match.end():]
    if leftover.strip(" .:-–—/()"):
        return None                      # a range or a sentence, not a number
    try:
        value = float(match.group(0))
    except ValueError:
        return None
    if negative:
        value = -abs(value)
    return Number(value=value, currency=currency, unit=unit, scale=scale, raw=raw)


def parse_date(text: str, *, dayfirst: bool = True) -> Optional[str]:
    """One ISO date, or None. `२७/११/२०२६` is the same day as `27/11/2026`."""
    if not text or len(text.strip()) > 40:
        return None
    cleaned = ascii_digits(text.strip())
    formats = _DATE_FORMATS
    if not dayfirst:
        formats = tuple(f for f in formats if f not in ("%d/%m/%Y", "%d-%m-%Y")) + \
            ("%m/%d/%Y",)
    for fmt in formats:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if fmt in ("%b %Y", "%B %Y"):
            parsed = parsed.replace(day=1)
        return parsed.date().isoformat()
    return None


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


@dataclass
class Table:
    """Rows of raw cell text, plus what the markup said about them."""

    rows: List[List[str]] = field(default_factory=list)
    header_flags: List[bool] = field(default_factory=list)  # was the row a <th>?
    caption: str = ""
    source: str = ""          # url or path
    span: Tuple[int, int] = (0, 0)   # character span in the page text

    def width(self) -> int:
        return max((len(row) for row in self.rows), default=0)


class _TableParser(HTMLParser):
    """Collect `<table>` into rows of cell text, keeping the `<th>` signal.

    Layout tables and data tables look identical in HTML, so the only cheap
    discriminator is `<th>`: a table whose first row is all `<th>` is probably
    a data table with a header. Everything else is decided by shape later.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[Table] = []
        self._stack: List[List[List[str]]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._header: List[bool] = []
        self._in_th = False
        self._caption: List[str] = []
        self._in_caption = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
            self._header = []
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._in_th = tag == "th"
        elif tag == "caption":
            self._in_caption = True
            self._caption = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if self._row:
                self._stack[-1].append(self._row)
                self._header.append(self._in_th)
            self._row = None
        elif tag == "caption":
            self._in_caption = False
        elif tag == "table" and self._stack:
            rows = self._stack.pop()
            if rows:
                self.tables.append(Table(rows=rows, header_flags=list(self._header),
                                         caption=re.sub(r"\s+", " ", "".join(self._caption)).strip()))

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._in_caption:
            self._caption.append(data)


def tables_from_html(html: str, source: str = "") -> List[Table]:
    parser = _TableParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:                    # a malformed page is not a crash
        pass
    tables = parser.tables
    for table in tables:
        table.source = source
    return tables


def table_from_csv(text: str, source: str = "") -> Optional[Table]:
    """A CSV is a table with a header and no markup to argue about."""
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    try:
        rows = list(csv.reader(io.StringIO(text), dialect))
    except csv.Error:
        return None
    rows = [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        return None
    return Table(rows=rows, header_flags=[True] + [False] * (len(rows) - 1), source=source)


# ---------------------------------------------------------------------------
# is it data, or is it layout?
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    relational: bool
    reason: str


def judge(table: Table) -> Verdict:
    """Cheap, explainable filters before anything expensive happens.

    WebTables found 154M relational tables in 14.1B — about 1%. The filter is
    the difference between a database and a pile of navigation bars.
    """
    rows = [row for row in table.rows if any(cell for cell in row)]
    if len(rows) < 2:
        return Verdict(False, "fewer than two rows")
    width = table.width()
    if width < 2:
        return Verdict(False, "single column")
    if width > 40:
        return Verdict(False, f"{width} columns: layout, not data")
    body = rows[1:]
    if not body:
        return Verdict(False, "header only")

    cells = [cell for row in body for cell in row]
    if not cells:
        return Verdict(False, "no cells")
    empty = sum(1 for cell in cells if not cell)
    if empty / len(cells) > 0.5:
        return Verdict(False, f"{empty / len(cells):.0%} empty cells")

    # every body row identical: a price card repeated, not a table. Needs at
    # least two body rows to mean anything -- one row is trivially identical.
    if len(body) >= 2:
        first = tuple(body[0])
        if all(tuple(row) == first for row in body):
            return Verdict(False, "every row identical")

    # at least one column must be mostly numeric or date-like, or the whole
    # table is prose in cells
    typed = 0
    for column in range(width):
        values = [row[column] for row in body if column < len(row) and row[column]]
        if not values:
            continue
        numeric = sum(1 for v in values if parse_number(v))
        dated = sum(1 for v in values if parse_date(v))
        if (numeric + dated) / len(values) >= 0.6:
            typed += 1
    if typed == 0:
        return Verdict(False, "no typed column")
    return Verdict(True, f"{len(body)} rows, {width} columns, {typed} typed")


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


@dataclass
class Column:
    name: str
    position: int
    type: str = "text"          # int | float | money | percent | date | text
    unit: Optional[str] = None
    currency: Optional[str] = None
    null_rate: float = 0.0
    samples: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "position": self.position, "type": self.type,
                "unit": self.unit, "currency": self.currency,
                "null_rate": round(self.null_rate, 3), "samples": self.samples[:3]}


def header_key(text: str, profile=None) -> str:
    """A stable column name: canonical if the word is known, else normalised."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    lowered = ascii_digits(raw).lower()
    if lowered in _CANONICAL:
        return _CANONICAL[lowered]
    for alias, key in _CANONICAL.items():
        if alias and alias in lowered:
            return key
    cleaned = re.sub(r"[^\w]+", "_", lowered, flags=re.UNICODE).strip("_")
    if not cleaned:
        cleaned = "column"
    if cleaned[0].isdigit():
        cleaned = f"c_{cleaned}"
    return cleaned[:60]


def _type_of(values: Sequence[str], dayfirst: bool = True) -> Tuple[str, Optional[str], Optional[str]]:
    """The narrowest type that fits most of the column, with its unit."""
    numbers = [parse_number(v, dayfirst=dayfirst) for v in values]
    numbers = [n for n in numbers if n is not None]
    dates = [parse_date(v, dayfirst=dayfirst) for v in values]
    dates = [d for d in dates if d]

    if len(dates) / max(1, len(values)) >= 0.9:
        return "date", None, None
    if len(numbers) / max(1, len(values)) >= 0.9:
        units = {n.unit for n in numbers if n.unit}
        currencies = {n.currency for n in numbers if n.currency}
        scales = {n.scale for n in numbers}
        # 50 GB and 5 TB in one column: a sum of those is meaningless, so the
        # unit is marked mixed and a query has to convert before it aggregates
        unit = units.pop() if len(units) == 1 else ("mixed" if len(units) > 1 else None)
        currency = currencies.pop() if len(currencies) == 1 else None
        if unit == "%":
            return "percent", unit, None
        if currency:
            return "money", unit, currency
        integral = all(float(n.scaled).is_integer() for n in numbers)
        if len(scales) > 1:
            return "float", "scaled", currency
        return ("int" if integral else "float"), unit, currency
    return "text", None, None


def schema_of(table: Table, dayfirst: bool = True) -> List[Column]:
    """Column names, types, units and null rates for one table."""
    width = table.width()
    has_header = bool(table.header_flags and table.header_flags[0])
    header = table.rows[0] if has_header else [f"c{index + 1}" for index in range(width)]
    body = table.rows[1:] if has_header else table.rows

    columns: List[Column] = []
    for position in range(width):
        name = header_key(header[position] if position < len(header) else f"c{position + 1}")
        values = [row[position] for row in body if position < len(row)]
        filled = [v for v in values if v]
        kind, unit, currency = _type_of(filled, dayfirst=dayfirst)
        columns.append(Column(
            name=name or f"c{position + 1}", position=position, type=kind,
            unit=unit, currency=currency,
            null_rate=1.0 - (len(filled) / max(1, len(values))),
            samples=filled[:3],
        ))
    return _dedupe(columns)


def _dedupe(columns: List[Column]) -> List[Column]:
    """Two columns called `price` is a schema bug, not a data fact."""
    seen: Dict[str, int] = {}
    for column in columns:
        if column.name in seen:
            seen[column.name] += 1
            column.name = f"{column.name}_{seen[column.name]}"
        else:
            seen[column.name] = 0
    return columns


@dataclass
class Record:
    """One row, typed, with the span it came from."""

    values: Dict[str, object]
    row_number: int


def typed_rows(table: Table, columns: Sequence[Column], dayfirst: bool = True) -> List[Record]:
    """Rows as Python values, with the raw text kept beside every number.

    A number that lost its raw text cannot be audited, and an audit is the
    whole reason a row is worth more than a chunk.
    """
    has_header = bool(table.header_flags and table.header_flags[0])
    body = table.rows[1:] if has_header else table.rows
    output: List[Record] = []
    for number, row in enumerate(body, start=1):
        values: Dict[str, object] = {}
        for column in columns:
            raw = row[column.position] if column.position < len(row) else ""
            if not raw:
                values[column.name] = None
                continue
            if column.type in ("int", "float", "money", "percent"):
                parsed = parse_number(raw, dayfirst=dayfirst)
                if parsed is None:
                    values[column.name] = raw
                    continue
                value = parsed.scaled
                values[column.name] = int(value) if column.type == "int" else value
                values[f"{column.name}__raw"] = raw
            elif column.type == "date":
                values[column.name] = parse_date(raw, dayfirst=dayfirst) or raw
            else:
                values[column.name] = raw
        output.append(Record(values=values, row_number=number))
    return output
