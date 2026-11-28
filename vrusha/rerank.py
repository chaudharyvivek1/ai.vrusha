"""Reranking: a second look at the top of the list, behind a seam.

Fusion gets the right chunk into the top twenty. It does not put it at rank one —
measured on this repository's own documentation, `recall@5` is 70% while
`recall@1` is 35%. Half of the questions that *could* be answered put the right
document somewhere in the top five and the wrong one first.

This module is the **seam**, not the reranker. Part 14 of `docs/PLAN-V3.md`:

- a protocol every reranker satisfies,
- a no-op that changes nothing, as the default,
- a registry so a backend is named in config and not hard-coded,
- and a guarantee that a broken reranker degrades instead of failing: a second
  stage that can take a query down is worse than no second stage.

Part 15 adds a lexical reranker that needs no model. Part 16 adds an optional
cross-encoder. Part 17 measures all three and decides whether any of them earned
its place — and "none of them" is a valid answer.

Why a seam first: without one, "add reranking" means editing the retriever, and
then there is no way to run the same 40 questions with and without it on the same
day. The seam is what makes the comparison possible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence

from .store.query import Hit


class Reranker(Protocol):
    """What a reranker must offer. Deliberately small."""

    name: str

    def available(self) -> bool:
        ...

    def rerank(self, query: str, hits: Sequence[Hit], k: int) -> List[Hit]:
        ...


@dataclass
class NoOpReranker:
    """Changes nothing, and says so.

    The default, because a reranker that has not been measured against the
    absence of a reranker has not been shown to do anything.
    """

    name: str = "none"

    def available(self) -> bool:
        return True

    def rerank(self, query: str, hits: Sequence[Hit], k: int) -> List[Hit]:
        return list(hits)[:k]


class RerankError(RuntimeError):
    """A reranker that cannot run. Never fatal: the retriever falls back."""


#: name -> factory. A reranker that needs a model registers itself here and is
#: only built when the config names it, so `import vrusha` stays dependency-free.
RERANKERS: Dict[str, callable] = {"none": NoOpReranker}


def register(name: str, factory) -> None:
    RERANKERS[name] = factory


def build_reranker(cfg: Optional[dict] = None) -> Reranker:
    """The configured reranker, or the no-op with the reason in its name."""
    block = ((cfg or {}).get("rerank", {}) or {})
    name = str(block.get("backend") or "none").strip().lower()
    factory = RERANKERS.get(name)
    if factory is None:
        raise RerankError(f"unknown reranker {name!r}; available: "
                          f"{', '.join(sorted(RERANKERS))}")
    reranker = factory()
    if not reranker.available():
        # not an error: a machine without the optional extra still answers
        # questions, just without the second stage
        return NoOpReranker()
    return reranker


def apply(reranker: Optional[Reranker], query: str, hits: Sequence[Hit],
          k: int, notes: Optional[list] = None) -> List[Hit]:
    """Run the second stage, and never let it take the query down.

    A reranker that raises is a bug in the reranker, not a reason to fail a
    search: the first stage already produced a defensible order, so the fallback
    is that order plus a note saying the second stage failed.
    """
    if reranker is None or not hits:
        return list(hits)[:k]
    try:
        reranked = reranker.rerank(query, hits, k)
    except Exception as exc:                 # noqa: BLE001 - see the docstring
        if notes is not None:
            notes.append(f"rerank {reranker.name} failed "
                         f"({type(exc).__name__}); using the fused order")
        return list(hits)[:k]
    if not isinstance(reranked, list):
        reranked = list(reranked)
    # the reranker may reorder and drop, but it may not invent: every returned
    # hit must be one it was given
    allowed = {hit.chunk_id for hit in hits}
    kept = [hit for hit in reranked if hit.chunk_id in allowed]
    if len(kept) != len(reranked) and notes is not None:
        notes.append(f"rerank {reranker.name} returned "
                     f"{len(reranked) - len(kept)} hit(s) it was not given; dropped")
    return kept[:k]


# ---------------------------------------------------------------------------
# Part 15: the lexical reranker — no model, no dependency
# ---------------------------------------------------------------------------


def _terms(text: str) -> List[str]:
    import re
    return [word.lower() for word in
            re.findall(r"[A-Za-z0-9_]+", text or "") if len(word) > 1]


@dataclass
class LexicalReranker:
    """Field-weighted BM25 over the candidates already retrieved.

    The first stage fuses BM25, vectors and addresses across the *whole* store.
    This looks again at the top twenty with three advantages it cannot have there:
    field weights (a title match counts three times a body match), a document
    frequency computed over the candidate set rather than the corpus, and a blend
    with the fused score so a reranker can refine but not overrule.

    No model, no dependency, and about a millisecond for twenty hits — which is
    the point: if this beats the no-op, it does so for free.
    """

    name: str = "lexical"
    #: how much the lexical signal may move the fused order. 0 = no-op, 1 = the
    #: fused score is ignored entirely. Deliberately below 1: the first stage
    #: knows things about the corpus this stage cannot see.
    #: 0.3 is measured, not guessed: `docs/work/part-15-lexical-rerank.md` sweeps
    #: it, and 0.5 gains another 0.5 points of nDCG at the cost of 5 points of
    #: passage@5 -- the right document higher, the right passage lower.
    alpha: float = 0.3
    k1: float = 1.2
    b: float = 0.75
    #: Field weights. Measured: 1, 2 and 3 give the same nDCG to three decimal
    #: places, so the title bias this was meant to fix is not what moves the
    #: numbers -- alpha is. Kept configurable so a corpus with real titles can
    #: re-measure rather than inherit this.
    title_weight: int = 3
    symbol_weight: int = 2

    def available(self) -> bool:
        return True

    def rerank(self, query: str, hits: Sequence[Hit], k: int) -> List[Hit]:
        hits = list(hits)
        if not hits:
            return []
        query_terms = _terms(query)
        if not query_terms:
            return hits[:k]

        documents = []
        for hit in hits:
            # a title match is worth three, a symbol two, the body one
            tokens: List[str] = []
            tokens += _terms(getattr(hit, "title", "")) * self.title_weight
            tokens += _terms(getattr(hit, "symbol", "")) * self.symbol_weight
            tokens += _terms(getattr(hit, "text", ""))
            documents.append(tokens)

        count = len(documents)
        average = sum(len(tokens) for tokens in documents) / max(1, count)
        scores: List[float] = []
        for tokens in documents:
            length = len(tokens) or 1
            total = 0.0
            for term in query_terms:
                frequency = tokens.count(term)
                if not frequency:
                    continue
                containing = sum(1 for other in documents if term in other)
                # BM25 idf, floored so a term in every candidate still counts a
                # little rather than going negative
                idf = max(0.05, __import__("math").log(
                    1.0 + (count - containing + 0.5) / (containing + 0.5)))
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * length / max(1e-9, average))
                total += idf * (frequency * (self.k1 + 1.0)) / max(1e-9, denominator)
            scores.append(total)

        peak = max(scores) or 1.0
        fused = [max(0.0, float(hit.scores.get("fused", hit.score))) for hit in hits]
        top = max(fused) or 1.0
        blended = []
        for index, hit in enumerate(hits):
            combined = ((1.0 - self.alpha) * (fused[index] / top)
                        + self.alpha * (scores[index] / peak))
            hit.scores["lexical"] = round(scores[index], 4)
            hit.scores["reranked"] = round(combined, 6)
            blended.append((combined, index, hit))
        blended.sort(key=lambda row: (-row[0], row[1]))
        return [hit for _score, _index, hit in blended[:k]]


register("lexical", LexicalReranker)


# ---------------------------------------------------------------------------
# Part 16: an optional cross-encoder, over ONNX rather than torch
# ---------------------------------------------------------------------------


class _OnnxScorer:
    """A cross-encoder that scores (query, passage) pairs, through ONNX.

    ONNX rather than torch on purpose: `onnxruntime` is ~15 MB and pulls no CUDA,
    while torch is ~800 MB and does. The rule this project has held since
    `docs/SURVEY.md` is that the core stays numpy-only — so this is an optional
    extra, imported only when the backend is chosen, and a machine without it
    keeps working with the no-op.
    """

    def __init__(self, model: str = "Xenova/ms-marco-MiniLM-L-6-v2",
                 filename: str = "onnx/model_quantized.onnx",
                 tokenizer: str = "tokenizer.json"):
        self.model_name = model
        self._session = None
        self._tokenizer = None
        self._filename = filename
        self._tokenizer_name = tokenizer

    def available(self) -> bool:
        try:
            import onnxruntime                          # noqa: F401
            import tokenizers                           # noqa: F401
            from huggingface_hub import hf_hub_download  # noqa: F401
            return True
        except Exception:                                # noqa: BLE001
            return False

    def _load(self):
        if self._session is not None:
            return
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        model_path = hf_hub_download(self.model_name, self._filename)
        tokenizer_path = hf_hub_download(self.model_name, self._tokenizer_name)
        self._session = onnxruntime.InferenceSession(
            model_path, providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

    def score(self, query: str, passages: Sequence[str],
              batch: int = 8) -> List[float]:
        """Score in batches: one pair at a time is slow, all sixty at once is a
        755 MB allocation on a 2 GB machine. Eight is the compromise, measured.
        """
        scores: List[float] = []
        for start in range(0, len(passages), max(1, batch)):
            scores.extend(self._score_batch(
                query, list(passages[start:start + max(1, batch)])))
        return scores

    def _score_batch(self, query: str, passages: Sequence[str]) -> List[float]:
        import numpy as np

        self._load()
        pairs = [(query, passage or "") for passage in passages]
        encodings = self._tokenizer.encode_batch(pairs)
        ids = np.asarray([encoding.ids for encoding in encodings], dtype=np.int64)
        mask = np.asarray([encoding.attention_mask for encoding in encodings],
                          dtype=np.int64)
        inputs = {}
        for entry in self._session.get_inputs():
            if entry.name == "input_ids":
                inputs[entry.name] = ids
            elif entry.name == "attention_mask":
                inputs[entry.name] = mask
            elif entry.name == "token_type_ids":
                inputs[entry.name] = np.zeros_like(ids)
        logits = self._session.run(None, inputs)[0]
        scores = np.asarray(logits).reshape(len(passages), -1)[:, 0]
        # a cross-encoder emits a logit; a sigmoid makes it comparable to the
        # fused score, which is what the blend needs
        return [float(1.0 / (1.0 + np.exp(-value))) for value in scores]


@dataclass
class CrossEncoderReranker:
    """The second stage with a model behind it. Opt-in, and measured.

    `scorer` is injectable so the ordering, blending and safety rules can be
    tested without a 23 MB download — and so a different cross-encoder can be
    swapped in without touching this class.
    """

    name: str = "cross-encoder"
    alpha: float = 0.5
    scorer: object = None

    def __post_init__(self):
        if self.scorer is None:
            self.scorer = _OnnxScorer()

    def available(self) -> bool:
        return bool(getattr(self.scorer, "available", lambda: False)())

    def rerank(self, query: str, hits: Sequence[Hit], k: int) -> List[Hit]:
        hits = list(hits)
        if not hits:
            return []
        scores = list(self.scorer.score(query, [getattr(hit, "text", "") or ""
                                                for hit in hits]))
        if len(scores) != len(hits):
            raise RerankError(f"scorer returned {len(scores)} scores for "
                              f"{len(hits)} hits")
        peak = max(scores) or 1.0
        fused = [max(0.0, float(hit.scores.get("fused", hit.score))) for hit in hits]
        top = max(fused) or 1.0
        blended = []
        for index, hit in enumerate(hits):
            combined = ((1.0 - self.alpha) * (fused[index] / top)
                        + self.alpha * (scores[index] / peak))
            hit.scores["cross_encoder"] = round(scores[index], 4)
            hit.scores["reranked"] = round(combined, 6)
            blended.append((combined, index, hit))
        blended.sort(key=lambda row: (-row[0], row[1]))
        return [hit for _score, _index, hit in blended[:k]]


register("cross-encoder", CrossEncoderReranker)
