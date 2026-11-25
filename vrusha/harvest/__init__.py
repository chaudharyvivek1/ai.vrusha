"""Harvest: search the web, fetch it politely, gate it, keep what survives."""

from .fetch import (
    Document,
    Fetcher,
    canonical_key,
    harvest_local,
    html_to_text,
    read_manifests,
    write_manifest,
)
from .gates import (
    GateReport,
    GateResult,
    detect_licence,
    evaluate,
    find_secrets,
    minhash_signature,
    redact,
)
from .search import SearchHit, search

__all__ = [
    "Document",
    "Fetcher",
    "GateReport",
    "GateResult",
    "SearchHit",
    "canonical_key",
    "detect_licence",
    "evaluate",
    "find_secrets",
    "harvest_local",
    "html_to_text",
    "minhash_signature",
    "read_manifests",
    "redact",
    "search",
    "write_manifest",
]
