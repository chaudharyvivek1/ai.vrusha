"""Ingest: documents in, addressed chunks out.

The only stage that touches the network is the harvest that produces the
documents; this file takes documents it is given and does four things to each:

    1. gate it      (policy, secrets, licence, language, size, quality, duplicate)
    2. chunk it     (one unit of meaning per row, per the language profile)
    3. embed it     (batch, one model call per run, not per chunk)
    4. store it     (document row, chunk rows, vectors, duplicate signatures)

A rejected document is written to the manifest with its reason and is not
stored. Rejections are the interesting part of the log: they are the record of
what the rules stopped.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .harvest.fetch import Document, write_manifest
from .harvest.gates import (
    band_keys,
    evaluate,
    minhash_signature,
    signature_hex,
)
from .languages import Registry
from .rules import RuleSet
from .store.chunk import chunk_text
from .store.db import Store


@dataclass
class IngestReport:
    documents: int = 0
    stored: int = 0
    rejected: int = 0
    chunks: int = 0
    embedded: int = 0
    by_reason: Dict[str, int] = field(default_factory=dict)
    rejected_rows: List[dict] = field(default_factory=list)

    def reject(self, doc: Document, reason: str) -> None:
        self.rejected += 1
        self.by_reason[reason] = self.by_reason.get(reason, 0) + 1
        self.rejected_rows.append({"url": doc.url, "reason": reason})

    def to_dict(self) -> dict:
        return {
            "documents": self.documents,
            "stored": self.stored,
            "rejected": self.rejected,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "by_reason": dict(sorted(self.by_reason.items())),
        }


def _profile_for(doc: Document, registry: Registry, detected_lang: str):
    """Code files use their language profile; prose uses the detected language."""
    if doc.kind == "code":
        profile = registry.for_path(doc.url.split("?")[0])
        if profile is not None:
            return profile
    return registry.maybe(detected_lang) or registry.get("en")


def ingest_documents(
    docs: Sequence[Document],
    store: Store,
    cfg: dict,
    rules: RuleSet,
    registry: Registry,
    embedder,
    *,
    licence: Optional[str] = None,
    manifest_dir: Optional[str] = None,
    verbose: bool = False,
) -> IngestReport:
    report = IngestReport()
    index = store.duplicate_index()
    pending: List[tuple[int, str]] = []      # (chunk_id, text) awaiting embedding
    manifest_rows: List[Document] = []

    for doc in docs:
        report.documents += 1
        if not doc.ok:
            report.reject(doc, doc.error or "empty document")
            manifest_rows.append(doc)
            continue

        is_code = doc.kind == "code"
        lang_code = "en"
        if not is_code:
            lang_code, _confidence = registry.detect(doc.text)
        else:
            profile = registry.for_path(doc.url.split("?")[0])
            lang_code = profile.code if profile else ""

        gate_report = evaluate(
            text=doc.text,
            url=doc.url,
            licence=doc.licence or licence,
            raw_chars=doc.raw_chars,
            rules=rules,
            registry=registry,
            index=index,
            is_code=is_code,
        )
        if not gate_report.passed:
            report.reject(doc, gate_report.summary())
            manifest_rows.append(doc)
            continue

        doc_id = store.add_document(
            url=doc.url,
            title=doc.title,
            text=doc.text,
            domain=doc.domain,
            kind=doc.kind,
            lang=lang_code,
            licence=doc.licence or licence,
            retrieved=doc.retrieved,
            status=doc.status,
            content_hash=doc.content_hash,
        )
        if doc_id is None:
            report.reject(doc, "already stored")
            manifest_rows.append(doc)
            continue

        profile = _profile_for(doc, registry, lang_code)
        chunks = chunk_text(doc.text, profile, cfg, url=doc.url)
        if not chunks:
            report.reject(doc, "no chunks produced")
            manifest_rows.append(doc)
            continue

        for chunk in chunks:
            chunk_id = store.add_chunk(
                doc_id,
                chunk.text,
                kind=chunk.kind,
                symbol=chunk.symbol,
                lang=chunk.lang or lang_code,
                ordinal=chunk.ordinal,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
            )
            signature = minhash_signature(chunk.text)
            store.set_signature(chunk_id, signature_hex(signature), band_keys(signature))
            pending.append((chunk_id, chunk.text))
            report.chunks += 1

        report.stored += 1
        manifest_rows.append(doc)
        if verbose:
            print(f"  stored {doc.url}  {len(chunks)} chunks")

    # one embedding batch for the whole run
    if pending:
        texts = [text for _chunk_id, text in pending]
        matrix = embedder.encode(texts)
        dtype = cfg.get("store", {}).get("vector_dtype", "float32")
        for (chunk_id, _text), vector in zip(pending, matrix):
            store.set_vector(chunk_id, vector, dtype)
        report.embedded = len(pending)

    store.commit()

    if manifest_dir:
        write_manifest(manifest_rows, manifest_dir)

    return report


def ingest_paths(
    paths: Sequence[str],
    store: Store,
    cfg: dict,
    rules: RuleSet,
    registry: Registry,
    embedder,
    *,
    licence: Optional[str] = None,
    manifest_dir: Optional[str] = None,
    verbose: bool = False,
) -> IngestReport:
    """Harvest local files and directories, then ingest them. The offline path."""
    from .harvest.fetch import harvest_local

    docs: List[Document] = []
    for path in paths:
        if not os.path.exists(path):
            continue
        docs.extend(harvest_local(path, licence=licence))
    return ingest_documents(docs, store, cfg, rules, registry, embedder,
                            licence=licence, manifest_dir=manifest_dir, verbose=verbose)
