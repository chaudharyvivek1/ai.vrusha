"""The store: addresses, chunks, vectors, passes, retrieval."""

from .address import (
    CODE_KIND_IDS,
    DOMAIN_IDS,
    DOMAINS,
    KIND_IDS,
    WEIGHTS,
    Address,
    AddressError,
    compose,
    sort_key,
    spread,
)
from .chunk import Chunk, chunk_text
from .db import SCHEMA_VERSION, Store
from .embed import Embedder, HashEmbedder, get_embedder
from .passes import Pass, PassContext, REGISTRY, describe, run_passes
from .query import Hit, Retrieval, build_context, retrieve

__all__ = [
    "CODE_KIND_IDS",
    "DOMAINS",
    "DOMAIN_IDS",
    "KIND_IDS",
    "REGISTRY",
    "SCHEMA_VERSION",
    "WEIGHTS",
    "Address",
    "AddressError",
    "Chunk",
    "Embedder",
    "HashEmbedder",
    "Hit",
    "Pass",
    "PassContext",
    "Retrieval",
    "Store",
    "build_context",
    "chunk_text",
    "compose",
    "describe",
    "get_embedder",
    "retrieve",
    "run_passes",
    "sort_key",
    "spread",
]
