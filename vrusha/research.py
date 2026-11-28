"""Research: a question in, an answer out, and every source that made it.

The pieces existed and did not talk to each other: `search` finds pages,
`harvest` fetches and gates them, `extract` reads their tables and formulae,
`ask` answers from the store, `verify --refetch` says whether a page still says
what it said. This is the command that composes them and — the part that
matters — returns the **sources as a first-class output**, not a footnote.

A source is not a URL. It is a URL, a title, a licence, when it was retrieved,
how many chunks and formulae came out of it, and whether re-fetching it today
still returns the same text. That last field is what makes an answer auditable
rather than merely cited.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .answer import ask
from .harvest.fetch import Fetcher
from .harvest.search import search
from .ingest import ingest_documents
from .store import records
from .store.db import url_hash
from .verify import _shingle_set


@dataclass
class Source:
    url: str
    title: str = ""
    licence: Optional[str] = None
    retrieved: str = ""
    status: int = 0
    chunks: int = 0
    tables: int = 0
    equations: int = 0
    verified: str = ""          # unchanged | drifted | rewritten | gone | skipped
    rejected: str = ""          # why the gates refused to store it, if they did

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "licence": self.licence,
                "retrieved": self.retrieved, "status": self.status,
                "chunks": self.chunks, "tables": self.tables,
                "equations": self.equations, "verified": self.verified,
                "rejected": self.rejected}


def _drift(before: str, after: str) -> str:
    """The same three-way split `verify --refetch` uses, on one page."""
    first = _shingle_set(before or "")
    second = _shingle_set(after or "")
    if not first or not second:
        return "rewritten"
    overlap = len(first & second) / max(1, len(first | second))
    if overlap >= 0.95:
        return "unchanged"
    if overlap >= 0.70:
        return "drifted"
    return "rewritten"


def research(store, embedder, cfg: dict, question: str, *, rules, registry,
             limit: int = 5, backend: str = "auto", top_k: int = 8,
             verify: bool = False, licence: Optional[str] = None,
             verbose: bool = True) -> Dict[str, object]:
    """Search, fetch, store, extract, answer — and report where it came from."""
    started = time.perf_counter()
    hits, notes = search(question, limit, cfg, backend=backend)
    if verbose:
        for note in notes:
            print(f"  · {note}")
    if not hits:
        return {"question": question, "answer": "", "refused": True,
                "reason": "no search results", "sources": [],
                "search_notes": notes, "backend": backend,
                "seconds": round(time.perf_counter() - started, 2)}

    fetcher = Fetcher(cfg)
    documents = []
    for hit in hits[:limit]:
        document = fetcher.get(hit.url)
        if document.ok:
            documents.append(document)
    if verbose:
        print(f"  fetched   {len(documents)} of {len(hits[:limit])} page(s)")

    # The gates are not relaxed for research. A page too short to be a document
    # is refused here for the same reason it is refused during a crawl, and the
    # refusal is reported per source rather than hidden -- the threshold lives in
    # `rules/levels/level-06/01-thresholds.json` if you want to change it.
    report = ingest_documents(documents, store, cfg, rules, registry, embedder,
                              licence=licence, verbose=False)
    if verbose:
        print(f"  stored    {report.stored} document(s), {report.chunks} chunk(s)")

    sources: List[Source] = []
    for document in documents:
        row = store.conn.execute(
            "SELECT doc_id, licence, retrieved FROM documents WHERE url_hash = ?",
            (url_hash(document.url),)).fetchone()
        if row is None:
            # the gates refused it: say so, with the reason, instead of quietly
            # returning a source list that does not mention the page at all
            reason = next(iter(report.by_reason or {}), "rejected by the gates")
            sources.append(Source(url=document.url, title=document.title or "",
                                  status=int(document.status or 0),
                                  rejected=str(reason)))
            continue
        doc_id = int(row["doc_id"])
        extracted = records.extract_document(store, doc_id, document.html,
                                             document.url)
        chunks = store.conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE doc_id = ? AND active = 1",
            (doc_id,)).fetchone()[0]
        source = Source(url=document.url, title=document.title or "",
                        licence=row["licence"], retrieved=row["retrieved"] or "",
                        status=int(document.status or 0), chunks=int(chunks),
                        tables=int(extracted["tables"]),
                        equations=int(extracted.get("equations", 0)))
        if verify:
            try:
                fresh = fetcher.get(document.url)
                source.verified = (_drift(document.text, fresh.text)
                                   if fresh.ok else "gone")
            except Exception:                  # noqa: BLE001 - a dead link is a fact
                source.verified = "gone"
        sources.append(source)

    answer = ask(store, embedder, question, cfg, top_k=top_k)
    # `Answer.hits` is a count, not a list: the citations are the record of what
    # the answer actually used, so those are what a source list should carry
    citations = []
    for citation in getattr(answer, "citations", []) or []:
        if isinstance(citation, dict):
            address = citation.get("address") or "unaddressed"
            citations.append(f"{citation.get('url', '')} [{address}]")
        else:
            citations.append(str(citation))
    elapsed = round(time.perf_counter() - started, 2)
    if verbose:
        print(f"  answered  in {elapsed}s from {len(sources)} source(s)")

    return {
        "question": question,
        "answer": getattr(answer, "text", "") or "",
        "method": getattr(answer, "method", ""),
        "refused": bool(getattr(answer, "refused", False)),
        "reason": getattr(answer, "reason", "") or "",
        "citations": citations,
        "chunks_used": int(getattr(answer, "hits", 0) or 0),
        "sources": [source.to_dict() for source in sources],
        "search_notes": notes,
        "backend": backend,
        "seconds": elapsed,
    }


def render(result: Dict[str, object]) -> str:
    """The human version: the answer first, then where each part of it came from."""
    lines: List[str] = [f"Q: {result['question']}", ""]
    answer = str(result.get("answer") or "").strip()
    if answer:
        lines.extend(f"   {line}" for line in answer.splitlines())
    elif result.get("refused"):
        lines.append(f"   refused: {result.get('reason') or 'no evidence'}")
    else:
        lines.append("   (no answer was composed; the sources below are what was found)")

    sources = result.get("sources") or []
    if sources:
        lines.extend(["", "Sources:"])
        for index, source in enumerate(sources, start=1):
            parts = [f"  [{index}] {source['url']}"]
            if source.get("title"):
                parts.append(f"      {source['title'][:90]}")
            facts = []
            if source.get("rejected"):
                facts.append(f"NOT STORED: {source['rejected']}")
            if source.get("licence"):
                facts.append(f"licence {source['licence']}")
            if source.get("chunks"):
                facts.append(f"{source['chunks']} chunk(s)")
            if source.get("tables"):
                facts.append(f"{source['tables']} table(s)")
            if source.get("equations"):
                facts.append(f"{source['equations']} formula(s)")
            if source.get("verified"):
                facts.append(f"verified: {source['verified']}")
            if facts:
                parts.append("      " + ", ".join(facts))
            lines.extend(parts)
    else:
        lines.extend(["", "Sources: none"])

    citations = result.get("citations") or []
    if citations:
        lines.extend(["", "Cited in the answer:"])
        lines.extend(f"  · {citation}" for citation in citations[:10])
    return "\n".join(lines)
