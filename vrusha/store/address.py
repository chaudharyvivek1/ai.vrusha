"""The V-address: one number for every unit of data, where the number *is*
its position in the tree of meaning.

The idea, in your words: a folder holds JavaScript; inside it, function data
sits at address 0.5; everything about functions lives under 0.5. Two addresses
far apart are different material; two addresses that differ only in the last
digit are near-duplicates. The address is not a label bolted onto a row — it is
the row's coordinates, and distance between coordinates is distance between
meanings.

Five segments, coarse to fine
-----------------------------
    d1 . d2 . d3 . d4 . d5
    |    |    |    |    +-- ordinal   1..N   order inside the cluster
    |    |    |    +-------- cluster   1..N   centroid bucket (what it is *about*)
    |    |    +------------- kind      1..4   module/class/function/block, doc/section/para/claim
    |    +------------------ family    1..N   language (python, javascript, english, hindi...)
    +----------------------- domain    1..5   prose / code / data / rules / talk

Read it left to right and the number gets more specific. `2.3.1.4.17` is:
domain 2 (code), family 3 (typescript), kind 1 (module), cluster 4, item 17.

Distance
--------
    d(a,b) = sum_i  w_i * |a_i - b_i|        w = (8, 4, 2, 1, 0.5)

The weights halve at each step, so a difference in the domain counts sixteen
times a difference in the ordinal. That is the property you asked for: the
left-hand digit carries the big difference, the right-hand digit carries the
small one. Maximum distance is 15.5 (every segment differs by one in the worst
case is smaller; the bound is sum(w) * 99 for large families, but the useful
range is 0..~40 and `similarity()` normalises it to 0..1).

Float form
----------
For display and for sorting in a spreadsheet, an address renders as a decimal
where the integer part is the domain and each later segment is two digits:

    (2, 3, 1, 4, 17)   ->   "2.03010417"
    (0, 5, 0, 0,  0)   ->   "0.05000000"     <- the "0.5" region you described
    (0, 5, 1, 0,  0)   ->   "0.05010000"     <- inside it

So "0.5" is a real prefix: everything starting "0.05" is under it. `parse`
accepts the dotted form, the float form and the short prefix form, and all
three of "0.5", "0.05" and "0.05000000" mean the same thing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

SEGMENTS = 5
WEIGHTS: Tuple[float, ...] = (8.0, 4.0, 2.0, 1.0, 0.5)

#: Domain ids. These are fixed forever; a new domain means a schema change.
DOMAINS = {
    1: "prose",     # natural-language knowledge
    2: "code",      # source code
    3: "data",      # structured records, tables, json
    4: "rules",     # the rule set itself, addressable like any other data
    5: "talk",      # conversation, questions, answers
}
DOMAIN_IDS = {name: idx for idx, name in DOMAINS.items()}

KINDS = {
    1: "document",
    2: "section",
    3: "paragraph",
    4: "claim",
}
KIND_IDS = {name: idx for idx, name in KINDS.items()}

CODE_KINDS = {
    1: "module",
    2: "class",
    3: "function",
    4: "block",
}
CODE_KIND_IDS = {name: idx for idx, name in CODE_KINDS.items()}

#: Reference only: sum of the weights, i.e. the cost of differing by one in
#: every segment. Distances are not normalised by it; see `similarity`.
UNIT_DISTANCE = sum(WEIGHTS)


class AddressError(ValueError):
    """Raised when a string cannot be read as an address."""


@dataclass(frozen=True, order=True)
class Address:
    """An immutable position in the tree. Always five segments.

    Construct with `Address.of(...)`, `Address.parse("2.3.1.4.17")` or
    `Address.root(domain=2)`.
    """

    segments: Tuple[int, ...]

    def __post_init__(self) -> None:
        segs = tuple(int(x) for x in self.segments)
        if not segs:
            raise AddressError("an address needs at least one segment")
        if len(segs) > SEGMENTS:
            raise AddressError(f"at most {SEGMENTS} segments, got {len(segs)}")
        for i, value in enumerate(segs):
            if value < 0:
                raise AddressError(f"segment {i + 1} is negative: {value}")
        # pad to five so comparisons and distances never need to special-case
        # depth. A shorter address is a prefix, and a prefix ends in zeros.
        object.__setattr__(self, "segments", segs + (0,) * (SEGMENTS - len(segs)))

    # ------------------------------------------------------------------
    # constructors
    # ------------------------------------------------------------------
    @classmethod
    def of(cls, *segments: int) -> "Address":
        return cls(tuple(segments))

    @classmethod
    def root(cls, domain: int = 1, family: int = 0) -> "Address":
        return cls((domain, family, 0, 0, 0))

    @classmethod
    def parse(cls, value: "str | Sequence[int] | Address") -> "Address":
        """Accept '2.3.1.4.17', '2.03010417', '0.5', '2.3', or a tuple."""
        if isinstance(value, Address):
            return value
        if isinstance(value, (tuple, list)):
            return cls(tuple(int(x) for x in value))
        text = str(value).strip()
        if not text:
            raise AddressError("empty address")
        parts = [p for p in text.split(".") if p != ""]
        if not parts:
            raise AddressError(f"not an address: {value!r}")
        segs: list[int] = []
        for part in parts:
            if not part.isdigit():
                raise AddressError(f"not an address: {value!r}")
            if len(part) <= 2:
                segs.append(int(part))
            else:
                # packed form: '03010417' -> 03 01 04 17
                if len(part) % 2:
                    part = part.zfill(len(part) + 1)
                segs.extend(int(part[i:i + 2]) for i in range(0, len(part), 2))
        return cls(tuple(segs))

    # ------------------------------------------------------------------
    # shape
    # ------------------------------------------------------------------
    @property
    def depth(self) -> int:
        """Number of meaningful segments (trailing zeros are not meaningful)."""
        depth = 0
        for i, value in enumerate(self.segments):
            if value:
                depth = i + 1
        return depth

    @property
    def domain(self) -> str:
        return DOMAINS.get(self.segments[0], f"domain-{self.segments[0]}")

    @property
    def family(self) -> int:
        return self.segments[1]

    @property
    def kind(self) -> str:
        table = CODE_KINDS if self.segments[0] == DOMAIN_IDS["code"] else KINDS
        return table.get(self.segments[2], f"kind-{self.segments[2]}")

    @property
    def cluster(self) -> int:
        return self.segments[3]

    @property
    def ordinal(self) -> int:
        return self.segments[4]

    # ------------------------------------------------------------------
    # tree operations
    # ------------------------------------------------------------------
    def prefix(self, depth: int) -> "Address":
        """The folder this address lives in, at `depth` segments."""
        if depth < 1:
            raise AddressError("depth starts at 1")
        depth = min(depth, SEGMENTS)
        return Address(self.segments[:depth] + (0,) * (SEGMENTS - depth))

    def parent(self) -> "Address":
        return self.prefix(max(1, self.depth - 1))

    def is_under(self, other: "Address") -> bool:
        """True when `self` sits inside `other`'s region."""
        d = other.depth
        return self.segments[:d] == other.segments[:d]

    def siblings(self) -> "Address":
        """The cluster this item belongs to."""
        return self.prefix(4)

    # ------------------------------------------------------------------
    # distance
    # ------------------------------------------------------------------
    def distance(self, other: "Address", weights: Sequence[float] = WEIGHTS) -> float:
        """Weighted L1. Coarse segments dominate."""
        return sum(
            w * abs(a - b)
            for w, a, b in zip(weights, self.segments, other.segments)
        )

    def similarity(self, other: "Address", weights: Sequence[float] = WEIGHTS) -> float:
        """1 / (1 + distance): 1.0 at the same address, falling off smoothly.

        Not distance divided by a maximum. The maximum is unbounded (a family
        id can be any integer), so dividing by it would call two unrelated
        domains 0.99 similar. The reciprocal has no such ceiling and reads
        correctly: neighbours 0.67, different domain 0.11.
        """
        return 1.0 / (1.0 + self.distance(other, weights))

    def nearest(self, others: Iterable["Address"], k: int = 5) -> list[tuple[float, "Address"]]:
        scored = [(self.distance(o), o) for o in others if o != self]
        scored.sort(key=lambda pair: (pair[0], pair[1].segments))
        return scored[:k]

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def __str__(self) -> str:
        """Trailing zeros are not printed: a prefix reads as the folder it is.

        (1, 1, 3, 1, 0) renders "1.1.3.1" and parses straight back to the same
        address, so the short form is lossless. Ordinals start at 1 and cluster
        ids at 1, so a trailing zero is always padding, never data.
        """
        depth = self.depth
        if depth == 0:
            return "0"
        return ".".join(str(x) for x in self.segments[:depth])

    def float_str(self) -> str:
        """'2.03010417' — the form you sort in a spreadsheet."""
        head = str(self.segments[0])
        tail = "".join(f"{x:02d}" for x in self.segments[1:])
        return f"{head}.{tail}"

    def label(self) -> str:
        """Human sentence for reports: 'code / family 3 / module / cluster 4 / #17'.

        Zero segments are left out, so a region reads as a region.
        """
        parts = [self.domain]
        if self.family:
            parts.append(f"family {self.family}")
        if self.segments[2]:
            parts.append(self.kind)
        if self.cluster:
            parts.append(f"cluster {self.cluster}")
        if self.ordinal:
            parts.append(f"#{self.ordinal}")
        return " / ".join(parts)

    def as_tuple(self) -> Tuple[int, ...]:
        return self.segments

    def to_dict(self) -> dict:
        return {
            "address": str(self),
            "float": self.float_str(),
            "domain": self.domain,
            "family": self.family,
            "kind": self.kind,
            "cluster": self.cluster,
            "ordinal": self.ordinal,
        }


def compose(domain: int, family: int, kind: int, cluster: int = 0, ordinal: int = 0) -> Address:
    """Build an address from its five named parts."""
    return Address((domain, family, kind, cluster, ordinal))


def sort_key(addr: Address) -> Tuple[int, ...]:
    """Natural tree order: a parent sorts before all of its children."""
    return addr.segments


def spread(addresses: Iterable[Address]) -> dict:
    """Summary statistics for a set of addresses — used by `vrusha report`."""
    addrs = list(addresses)
    if not addrs:
        return {"count": 0}
    domains: dict[str, int] = {}
    families: dict[tuple[str, int], int] = {}
    clusters: dict[str, int] = {}
    for a in addrs:
        domains[a.domain] = domains.get(a.domain, 0) + 1
        key = (a.domain, a.family)
        families[key] = families.get(key, 0) + 1
        clusters[str(a.prefix(4))] = clusters.get(str(a.prefix(4)), 0) + 1
    return {
        "count": len(addrs),
        "domains": dict(sorted(domains.items())),
        "families": {f"{d}:{f}": n for (d, f), n in sorted(families.items())},
        "clusters": len(clusters),
        "deepest": str(max(addrs, key=lambda a: a.depth)),
        "widest_cluster": max(clusters.items(), key=lambda kv: kv[1]),
    }
