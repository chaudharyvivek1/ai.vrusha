"""The composer: an answer built from retrieved text, with no model at all.

This exists because a trained model is the last thing you get, not the first.
The store is useful on day one; the composer makes it answer on day one. It is
extractive and deterministic: it picks the sentences from the retrieved chunks
that best cover the question, orders them, and cites every one.

It will not write prose. It will not generalise. What it does do is prove that
retrieval is returning the right material — which is the actual bug 90% of the
time — before a model is in the loop confusing the diagnosis.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..store.query import Hit

_STOP = {
    "the", "a", "an", "of", "to", "in", "is", "are", "was", "were", "and", "or",
    "for", "on", "at", "by", "with", "from", "that", "this", "it", "as", "be",
    "what", "which", "how", "why", "when", "who", "does", "do", "did", "can",
    "could", "should", "would", "i", "you", "we", "they", "my", "your",
}

_SENTENCE = re.compile(r"(?<=[.!?।])\s+")


@dataclass
class ComposedAnswer:
    question: str
    answer: str
    citations: List[dict] = field(default_factory=list)
    method: str = "extractive"
    refused: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.answer,
            "method": self.method,
            "refused": self.refused,
            "reason": self.reason,
            "citations": self.citations,
        }


def _terms(text: str) -> Dict[str, int]:
    words = [w for w in re.findall(r"[a-zA-Z0-9_]+", text.lower()) if len(w) > 2 and w not in _STOP]
    out: Dict[str, int] = {}
    for word in words:
        out[word] = out.get(word, 0) + 1
    return out


CODE_KINDS = ("module", "class", "function", "block")


def _sentences(text: str) -> List[str]:
    """Split prose into sentences. Code never reaches this function."""
    parts = [p.strip() for p in _SENTENCE.split(text) if p.strip()]
    out: List[str] = []
    for part in parts:
        if len(part) > 400:
            out.extend(part[i:i + 400] for i in range(0, len(part), 400))
        else:
            out.append(part)
    return out


def _code_excerpt(text: str, limit: int = 700) -> str:
    """A whole code unit, trimmed at a line boundary. Never cut mid-token."""
    body = text.strip()
    if len(body) <= limit:
        return body
    cut = body.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return body[:cut].rstrip() + "\n    ..."


def _score(sentence: str, question_terms: Dict[str, int]) -> float:
    words = _terms(sentence)
    if not words:
        return 0.0
    overlap = sum(min(question_terms[w], words[w]) for w in question_terms if w in words)
    if not overlap:
        return 0.0
    # length-normalised, so a long paragraph does not win on volume alone
    return overlap / math.sqrt(len(words))


def compose_answer(question: str, hits: Sequence[Hit], cfg: Optional[dict] = None,
                   max_chars: int = 1200) -> ComposedAnswer:
    cfg = cfg or {}
    refuse_below = float(cfg.get("query", {}).get("refuse_below", 0.02))
    if not hits:
        return ComposedAnswer(
            question=question, answer="",
            refused=True, reason="nothing retrieved above the refusal threshold",
        )

    question_terms = _terms(question)
    ranked: List[tuple[float, int, str, bool]] = []
    for index, hit in enumerate(hits, start=1):
        if hit.kind in CODE_KINDS:
            # one candidate per code unit: the unit itself
            excerpt = _code_excerpt(hit.text)
            if len(excerpt) < 20:
                continue
            score = _score(excerpt, question_terms)
            if score > 0:
                ranked.append((score, index, excerpt, True))
            continue
        for sentence in _sentences(hit.text):
            if len(sentence) < 30:
                continue
            score = _score(sentence, question_terms)
            if score > 0:
                ranked.append((score, index, sentence, False))

    if not ranked:
        # Nothing covers the question. Say so, and offer the nearest material as
        # a pointer rather than dressing it up as an answer.
        top = hits[0]
        preview = re.sub(r"\s+", " ", top.text)[:300]
        return ComposedAnswer(
            question=question,
            answer=(f"Nothing in the store answers this. The closest material is "
                    f"[1] {top.title or top.url}: {preview} [1]"),
            citations=[{"index": 1, "url": top.url, "address": str(top.address) if top.address else None,
                        "title": top.title}],
            method="refusal-nearest",
            refused=True,
            reason="no retrieved sentence covers the question",
        )

    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen: List[tuple[int, str, bool]] = []
    seen: set[str] = set()
    total = 0
    for score, index, text, is_code in ranked:
        key = text[:80].lower()
        if key in seen:
            continue
        if total + len(text) > max_chars:
            break
        seen.add(key)
        chosen.append((index, text, is_code))
        total += len(text)
        if len(chosen) >= 6:
            break

    # order the answer by source order, which reads better than score order
    chosen.sort(key=lambda item: item[0])
    parts: List[str] = []
    for index, text, is_code in chosen:
        if is_code:
            language = next((h.lang for h in hits[index - 1:index] if h.lang), "")
            parts.append(f"```{language}\n{text}\n``` [{index}]")
        else:
            parts.append(f"{text} [{index}]")
    body = "\n\n".join(parts)

    citations = []
    used = sorted({index for index, _t, _c in chosen})
    for index in used:
        hit = hits[index - 1]
        citations.append({
            "index": index,
            "url": hit.url,
            "title": hit.title,
            "address": str(hit.address) if hit.address else None,
            "licence": hit.licence,
        })

    if hits[0].score < refuse_below:
        return ComposedAnswer(
            question=question, answer="", refused=True,
            reason=f"top score {hits[0].score:.4f} is below the refusal threshold {refuse_below}",
        )

    return ComposedAnswer(question=question, answer=body, citations=citations)


def format_citations(citations: Sequence[dict]) -> str:
    lines = []
    for citation in citations:
        address = citation.get("address") or "unaddressed"
        title = citation.get("title") or citation.get("url")
        lines.append(f"[{citation['index']}] {title} — {citation['url']}  ({address})")
    return "\n".join(lines)
