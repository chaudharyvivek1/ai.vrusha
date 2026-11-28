"""JSON-LD: the page already told you its schema.

An HTML table makes you infer a header, guess types and hope the units line up.
A `application/ld+json` block makes you do none of that: schema.org says the
thing is a `Product`, its `offers.price` is `4999`, its `priceCurrency` is
`INR`, and its `datePublished` is a date. That is a better source than any
table on the same page, and it is the one most sites already publish for search
engines.

So this module reads it and turns it into the same shape the table extractor
produces — a schema plus typed rows — with two differences:

1. **Grouped by `@type`.** All the `Product` entities on a page are rows of one
   `Product` table; the `Organization` becomes its own. A page with ten products
   is one table with ten rows, not ten tables with one row.
2. **Flattened with a path.** `offers.price` becomes `offers_price`, and an
   array of offers takes the first with a `_2`, `_3` suffix on the rest. A
   nested object is not a JSON blob in a cell; it is columns.

What it does not do: fetch remote `@context`, resolve `@id` references across
documents, or validate against the schema.org vocabulary. Those are three
different projects and none of them is needed to read a price.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .extract import parse_date, parse_number

#: Keys that describe the document rather than the thing. `@context` is a
#: pointer, `@type` is the table name, and keeping either as a column would put
#: the same value in every row.
_SKIP_KEYS = {"@context", "@type"}

_SCRIPT = re.compile(
    r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL)

#: Depth limit for flattening. A schema.org tree deeper than this is either
#: circular or somebody's nested CMS export; either way it is not a table.
_MAX_DEPTH = 4
_MAX_COLUMNS = 60


@dataclass
class Entity:
    """One schema.org thing, flattened, with its type."""

    type: str
    values: Dict[str, Any]
    identifier: str = ""
    #: True when the entity was a direct child of the block or of `@graph`.
    #: A nested object with its own `@type` is already a column of its parent,
    #: so it only earns a table of its own when there are several of them.
    top_level: bool = False


@dataclass
class EntityTable:
    """All the entities of one `@type` on a page: a schema plus rows."""

    type: str
    entities: List[Entity] = field(default_factory=list)
    source: str = ""

    def columns(self) -> List[str]:
        seen: List[str] = []
        for entity in self.entities:
            for key in entity.values:
                if key not in seen:
                    seen.append(key)
                if len(seen) >= _MAX_COLUMNS:
                    return seen
        return seen


def blocks(html: str) -> List[Any]:
    """Every JSON-LD block on the page, parsed. A broken block is skipped.

    One malformed block must not cost the other nine: sites ship a valid
    `Organization` and an invalid `BreadcrumbList` in the same page all the
    time.
    """
    output: List[Any] = []
    for match in _SCRIPT.finditer(html or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        # a CDATA wrapper or an HTML comment around the JSON is common
        raw = re.sub(r"^\s*(<!--|//\s*<!\[CDATA\[)", "", raw)
        raw = re.sub(r"(-->|//\s*\]\]>)\s*$", "", raw).strip().rstrip(";")
        try:
            output.append(json.loads(raw))
        except ValueError:
            continue
    return output


def _types(node: Dict[str, Any]) -> List[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _flatten(node: Any, prefix: str = "", depth: int = 0,
             out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Nested objects become `a_b` columns; arrays of scalars become a list."""
    out = {} if out is None else out
    if depth > _MAX_DEPTH or not isinstance(node, dict):
        return out
    for key, value in node.items():
        if key in _SKIP_KEYS:
            continue
        name = f"{prefix}{key}" if not prefix else f"{prefix}_{key}"
        if isinstance(value, dict):
            _flatten(value, name, depth + 1, out)
        elif isinstance(value, list):
            if not value:
                continue
            if all(isinstance(item, (str, int, float, bool)) for item in value):
                out[name] = ", ".join(str(item) for item in value)
            else:
                for number, item in enumerate(value[:4], start=1):
                    if isinstance(item, dict):
                        _flatten(item, name if number == 1 else f"{name}_{number}",
                                 depth + 1, out)
                    else:
                        out[f"{name}_{number}"] = str(item)
        else:
            out[name] = value
    return out


def entities(html: str, source: str = "") -> List[EntityTable]:
    """Group every schema.org entity on the page by its `@type`."""
    grouped: Dict[str, List[Entity]] = {}
    order: List[str] = []

    def visit(node: Any, depth: int = 0, top: bool = False) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(node, list):
            for item in node:
                visit(item, depth + 1, top)
            return
        if not isinstance(node, dict):
            return
        if "@graph" in node and isinstance(node["@graph"], list):
            visit(node["@graph"], depth + 1, True)
        types = _types(node)
        if types:
            values = _flatten(node)
            for type_name in types:
                if type_name not in grouped:
                    grouped[type_name] = []
                    order.append(type_name)
                grouped[type_name].append(Entity(
                    type=type_name, values=values,
                    identifier=str(node.get("@id") or ""), top_level=top))
        # a nested object with its own @type (an Offer inside a Product) is
        # already a column of its parent; it is still visited so that a page
        # with several of them can become a table of its own
        for key, value in node.items():
            if key in ("@graph",) or key in _SKIP_KEYS:
                continue
            if isinstance(value, (dict, list)):
                visit(value, depth + 1, False)

    for block in blocks(html):
        visit(block, 0, True)

    tables: List[EntityTable] = []
    for name in order:
        entities_of_type = grouped[name]
        # one nested object is a column of its parent, not a table of one row
        if len(entities_of_type) == 1 and not entities_of_type[0].top_level:
            continue
        tables.append(EntityTable(type=name, entities=entities_of_type,
                                  source=source))
    return tables


# ---------------------------------------------------------------------------
# typing the columns
# ---------------------------------------------------------------------------


def column_type(values: Sequence[Any]) -> Tuple[str, Optional[str], Optional[str]]:
    """The narrowest type for a JSON-LD column.

    The values arrive already typed, which is the advantage over a table: a
    number is a number. Strings still have to be read, because `price` is very
    often `"₹1,23,456"` and `datePublished` is very often `"2026-11-27"`.
    """
    filled = [value for value in values if value not in (None, "")]
    if not filled:
        return "text", None, None

    numbers, dates = [], []
    for value in filled:
        if isinstance(value, bool):
            return "bool", None, None
        if isinstance(value, (int, float)):
            numbers.append(float(value))
        elif isinstance(value, str):
            parsed = parse_number(value)
            if parsed is not None:
                numbers.append(parsed.scaled)
            if parse_date(value):
                dates.append(value)

    if len(dates) / len(filled) >= 0.9:
        return "date", None, None
    if len(numbers) / len(filled) >= 0.9:
        currencies = {parse_number(v).currency for v in filled
                      if isinstance(v, str) and parse_number(v)}
        currencies.discard(None)
        if currencies:
            return "money", None, currencies.pop()
        integral = all(float(number).is_integer() for number in numbers)
        return ("int" if integral else "float"), None, None
    return "text", None, None


def coerce(value: Any, kind: str) -> Any:
    """Store the value in the type the column claims, keeping strings readable."""
    if value is None or value == "":
        return None
    if kind in ("int", "float", "money"):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value) if kind == "int" else float(value)
        parsed = parse_number(str(value))
        if parsed is None:
            return value
        return int(parsed.scaled) if kind == "int" else parsed.scaled
    if kind == "date":
        return parse_date(str(value)) or value
    if kind == "bool":
        return bool(value)
    return value
