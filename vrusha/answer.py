"""Answering: retrieve, then answer with the model if there is one, else compose.

The order is fixed and deliberate:

    1. retrieve       three signals, fused by rank
    2. refuse         nothing above the threshold means "I do not have this"
    3. answer         a checkpoint if one is loaded, the composer otherwise
    4. attribute      every answer carries its sources, model or not

A model with no grounding will invent a citation. A composer cannot. Both
carry the same citation block, so the difference is visible to the reader.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .generate.compose import ComposedAnswer, compose_answer, format_citations
from .store.address import Address
from .store.db import Store
from .store.query import build_context, retrieve

SYSTEM_PROMPT = (
    "You answer only from the numbered context. Cite the number after each "
    "claim, like [2]. If the context does not contain the answer, say so "
    "plainly and do not guess."
)


@dataclass
class Answer:
    question: str
    text: str
    citations: List[dict] = field(default_factory=list)
    method: str = "extractive"
    refused: bool = False
    reason: str = ""
    hits: int = 0

    def render(self) -> str:
        parts = [self.text.strip()]
        if self.citations:
            parts.append("")
            parts.append(format_citations(self.citations))
        return "\n".join(parts).strip()

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.text,
            "method": self.method,
            "refused": self.refused,
            "reason": self.reason,
            "hits": self.hits,
            "citations": self.citations,
        }


def _load_model(checkpoint: str, device: str = "cpu"):
    from .generate.model import TinyLM
    model, payload = TinyLM.from_checkpoint(checkpoint, map_location=device)
    return model, payload


def ask(
    store: Store,
    embedder,
    question: str,
    cfg: dict,
    *,
    checkpoint: Optional[str] = None,
    address: Optional[Address] = None,
    top_k: Optional[int] = None,
    device: str = "cpu",
    router=None,
) -> Answer:
    retrieval = retrieve(store, embedder, question, cfg, address_hint=address,
                         top_k=top_k, router=router)
    if retrieval.empty:
        return Answer(
            question=question, text="I do not have this in the store.",
            method="refusal", refused=True,
            reason="; ".join(retrieval.notes) or "no hits",
        )

    if checkpoint and os.path.exists(checkpoint):
        try:
            model, payload = _load_model(checkpoint, device)
            tokenizer_path = (payload.get("extra") or {}).get("tokenizer") or ""
            if tokenizer_path and os.path.exists(tokenizer_path):
                from .tokens.bpe import Tokenizer
                tokenizer = Tokenizer.load(tokenizer_path)
                context = build_context(retrieval.hits)
                prompt = f"Context:\n{context}\n\nQuestion: {question}\nAnswer:"
                ids = tokenizer.encode(prompt, add_specials=True)
                import torch
                idx = torch.tensor([ids], dtype=torch.long, device=device)
                out = model.generate(idx, max_new_tokens=120, temperature=0.7,
                                     top_k=40, eos_id=2)
                text = tokenizer.decode(out[0, len(ids):].tolist()).strip()
                if text:
                    return Answer(
                        question=question, text=text,
                        citations=[{"index": i + 1, "url": h.url, "title": h.title,
                                    "address": str(h.address) if h.address else None,
                                    "licence": h.licence}
                                   for i, h in enumerate(retrieval.hits[:3])],
                        method=f"model:{os.path.basename(checkpoint)}",
                        hits=len(retrieval.hits),
                    )
        except Exception as exc:                     # a bad checkpoint is not a dead end
            note = f"checkpoint unusable ({type(exc).__name__}), composed instead"
        else:
            note = ""
    else:
        note = ""

    composed: ComposedAnswer = compose_answer(question, retrieval.hits, cfg)
    return Answer(
        question=question,
        text=composed.answer or "I do not have this in the store.",
        citations=composed.citations,
        method=composed.method,
        refused=composed.refused,
        reason=composed.reason or note,
        hits=len(retrieval.hits),
    )
