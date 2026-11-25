"""The store: addresses, chunks, vectors, passes, retrieval."""

from .address import (
    CODE_KIND_IDS,
    DOMAIN_IDS,
    DOMAINS,
    KIND_IDS,
    MAX_SEGMENT,
    WEIGHTS,
    Address,
    AddressError,
    centroid_table,
    compose,
    semantic_similarity,
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
    "MAX_SEGMENT",
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
    "centroid_table",
    "chunk_text",
    "compose",
    "semantic_similarity",
    "describe",
    "get_embedder",
    "retrieve",
    "run_passes",
    "sort_key",
    "spread",
]
