"""Evaluation: the numbers that make every other claim checkable.

Part 1 of `docs/PLAN-V3.md`. The repository has recall@1/@3 and MRR in
`bench/embedders.py`; that was enough to compare three embedders and not enough to
judge a pipeline. This module adds the standard retrieval metrics, plus the two
that matter for a system that answers rather than ranks:

- **citation accuracy** — did the answer cite the document that holds the answer?
- **faithfulness** — is the answer's text supported by the retrieved context, or
  did the model add something nobody retrieved?

Faithfulness here is a **lexical proxy**: the share of the answer's content words
that appear in the cited chunks. It is not a judgement of truth, it is a check for
invention, and it is labelled as a proxy everywhere it appears because a number
that pretends to be more than it is will be trusted for more than it is.

Every metric is written to be checkable by hand: `tests/test_evaluate.py` computes
the same values from a worked example on paper.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_WORD = re.compile(r"[a-z0-9\u0900-\u097F]+", re.IGNORECASE)

#: Words that carry no evidence. Kept deliberately short: a long stoplist makes a
#: lexical faithfulness score look better than it is.
_STOP = frozenset("""a an and are as at be by for from has have in is it its of on or
that the their there these this to was were will with what which who how why""".split())


@dataclass
class Item:
    """One labelled question."""

    question: str
    #: Document that holds the answer, as stored (`documents.url` or title).
    document: str = ""
    #: Words that must appear in a correct passage. The passage check is a
    #: heuristic, so it is kept explicit and visible rather than hidden in a
    #: model.
    keywords: Tuple[str, ...] = ()
    note: str = ""

    def relevant(self, url: str, title: str) -> bool:
        wanted = (self.document or "").strip().lower()
        if not wanted:
            return False
        return wanted in (url or "").lower() or wanted in (title or "").lower()


@dataclass
class Outcome:
    item: Item
    ranked: List[str] = field(default_factory=list)      # documents, best first
    ranks: List[int] = field(default_factory=list)       # chunk ids
    answer: str = ""
    cited: List[str] = field(default_factory=list)       # documents the answer cited
    passage_hit: bool = False
    seconds: float = 0.0

    @property
    def first_rank(self) -> int:
        """1-based rank of the first relevant document, or 0 for none."""
        for index, document in enumerate(self.ranked, start=1):
            if self.item.relevant(document, document):
                return index
        return 0


# ---------------------------------------------------------------------------
# the metrics
# ---------------------------------------------------------------------------


def recall_at_k(ranked: Sequence[str], item: Item, k: int) -> float:
    """Did the top k contain the right document? Binary, per question."""
    if not item.document:
        return 0.0
    for document in ranked[:k]:
        if item.relevant(document, document):
            return 1.0
    return 0.0


def precision_at_k(ranked: Sequence[str], item: Item, k: int) -> float:
    """Of the k shown, how many were right. With one right document, this is 1/k."""
    if k <= 0:
        return 0.0
    hits = sum(1 for document in ranked[:k] if item.relevant(document, document))
    return hits / float(k)


def reciprocal_rank(ranked: Sequence[str], item: Item) -> float:
    for index, document in enumerate(ranked, start=1):
        if item.relevant(document, document):
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: Sequence[str], item: Item, k: int) -> float:
    """Normalised discounted gain, binary relevance.

    DCG rewards a hit at rank 1 more than a hit at rank 5; IDCG is the best
    possible ordering for this question, so a single relevant document found
    first scores 1.0 and found fifth scores 1/log2(6) ≈ 0.386.
    """
    if not item.document or k <= 0:
        return 0.0
    dcg = 0.0
    for index, document in enumerate(ranked[:k], start=1):
        if item.relevant(document, document):
            dcg += 1.0 / math.log2(index + 1)
    ideal = 1.0                      # one relevant document, at rank 1
    return min(1.0, dcg / ideal)


def citation_accuracy(outcome: Outcome) -> Optional[float]:
    """Did the answer cite the document that holds the answer?

    `None` when the answer cited nothing, because "cited nothing" and "cited the
    wrong thing" are different failures and averaging them hides both.
    """
    if not outcome.cited:
        return None
    for document in outcome.cited:
        if outcome.item.relevant(document, document):
            return 1.0
    return 0.0


def _content_words(text: str) -> List[str]:
    return [word.lower() for word in _WORD.findall(text or "")
            if word.lower() not in _STOP and len(word) > 2]


def faithfulness(answer: str, context: str) -> Optional[float]:
    """Share of the answer's content words that appear in the context.

    A **proxy**, not a judgement: it catches an answer that invents names,
    numbers or terms the retrieved text never contained. It cannot catch an
    answer that is faithful in words and wrong in meaning.
    """
    words = _content_words(answer)
    if not words:
        return None
    context_words = set(_content_words(context))
    if not context_words:
        return 0.0
    supported = sum(1 for word in words if word in context_words)
    return supported / float(len(words))


def passage_hit(item: Item, texts: Iterable[str]) -> bool:
    """Does any retrieved passage contain the words a correct answer needs?"""
    if not item.keywords:
        return False
    wanted = {word.lower() for word in item.keywords}
    for text in texts:
        found = set(_content_words(text))
        if wanted <= found:
            return True
    return False


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def summarise(outcomes: Sequence[Outcome]) -> Dict[str, float]:
    """One row of the table: every metric, averaged over the questions."""
    if not outcomes:
        return {}
    count = float(len(outcomes))

    def mean(values: List[float]) -> float:
        return sum(values) / count if values else 0.0

    citations = [value for value in
                 (citation_accuracy(outcome) for outcome in outcomes)
                 if value is not None]
    return {
        "questions": len(outcomes),
        "recall@1": mean([recall_at_k(o.ranked, o.item, 1) for o in outcomes]),
        "recall@5": mean([recall_at_k(o.ranked, o.item, 5) for o in outcomes]),
        "recall@10": mean([recall_at_k(o.ranked, o.item, 10) for o in outcomes]),
        "precision@5": mean([precision_at_k(o.ranked, o.item, 5) for o in outcomes]),
        "mrr": mean([reciprocal_rank(o.ranked, o.item) for o in outcomes]),
        "ndcg@10": mean([ndcg_at_k(o.ranked, o.item, 10) for o in outcomes]),
        "passage@5": mean([1.0 if o.passage_hit else 0.0 for o in outcomes]),
        # citations and faithfulness are reported separately, with their counts,
        # because an average over "no answer" is not an average
        "citation_accuracy": (sum(citations) / len(citations)) if citations else 0.0,
        "cited_questions": len(citations),
        "seconds": mean([o.seconds for o in outcomes]),
    }


def render(summary: Dict[str, float], title: str = "") -> str:
    """The table, with each metric's definition beside it."""
    if not summary:
        return "no questions evaluated"
    lines = []
    if title:
        lines.append(title)
        lines.append("")
    lines.append(f"{'metric':<18}{'value':>9}  definition")
    lines.append("-" * 78)
    rows = [
        ("recall@1", "the right document is the top hit"),
        ("recall@5", "the right document is in the top five"),
        ("recall@10", "the right document is in the top ten"),
        ("precision@5", "share of the top five that are right"),
        ("mrr", "1/rank of the first right document"),
        ("ndcg@10", "position-weighted gain over the top ten"),
        ("passage@5", "a top-five passage contains the needed words"),
        ("citation_accuracy", "the answer cited the document holding the answer"),
    ]
    cited = int(summary.get("cited_questions", 0) or 0)
    for name, definition in rows:
        if name not in summary:
            continue
        value = summary[name]
        if name == "citation_accuracy" and not cited:
            # nothing was cited, so the accuracy is undefined -- printing 0%
            # would read as "every citation was wrong", which is a different
            # and much worse finding
            lines.append(f"{name:<18}{'n/a':>9}  {definition} (no answer cited)")
            continue
        shown = f"{value:.0%}" if name != "mrr" else f"{value:.3f}"
        lines.append(f"{name:<18}{shown:>9}  {definition}")
    if "cited_questions" in summary:
        lines.append(f"{'cited':<18}{cited:>9}"
                     f"  questions whose answer cited anything")
    if "seconds" in summary:
        lines.append(f"{'seconds':<18}{summary['seconds']:>9.2f}  mean per question")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the built-in set: twelve questions about this repository
# ---------------------------------------------------------------------------

#: The same twelve questions `bench/embedders.py` uses, phrased the way a person
#: would phrase them — deliberately avoiding the words the target document uses,
#: because a question that repeats the title only tests the keyword index.
#: Part 2 of `docs/PLAN-V3.md` grows this to forty, with passage labels.
DOCS_SET: Tuple[Item, ...] = (
    Item("how do I stop the crawler from hammering a site", "operations.md",
         ("rate", "limit")),
    Item("why are my results wrong after I added new pages", "ann.md",
         ("stale", "rebuild")),
    Item("what happens when two pieces of text get the same number", "address.md",
         ("collision", "ordinal")),
    Item("can this run on a laptop without a graphics card", "model.md",
         ("cpu", "model")),
    Item("how do I know a page is allowed to be kept", "rules.md",
         ("licence", "rule")),
    Item("splitting one long article into smaller pieces", "architecture.md",
         ("chunk", "split")),
    Item("teaching a small language model on my own text", "model.md",
         ("train", "tokenizer")),
    Item("what am I allowed to do with the copied material", "rules.md",
         ("licence", "permission")),
    Item("finding equations inside a research paper", "math.md",
         ("formula", "canonical")),
    Item("answering a question and showing where it came from", "research.md",
         ("source", "licence")),
    Item("what do the numbers in the tree mean", "address.md",
         ("segment", "address")),
    Item("making search faster on a cheap machine", "ann.md",
         ("index", "nprobe")),
)


def run(store, embedder, cfg: dict, items: Sequence[Item] = DOCS_SET,
        *, top_k: int = 5, with_answers: bool = False) -> List[Outcome]:
    """Ask every question, and record where the right answer came from.

    Retrieval is measured on every question; the answer and its citations only
    when `with_answers` is set, because composing an answer costs a model call
    and the retrieval numbers do not depend on it.
    """
    import time

    from .store.query import retrieve

    outcomes: List[Outcome] = []
    for item in items:
        started = time.perf_counter()
        result = retrieve(store, embedder, item.question, cfg, top_k=top_k)
        hits = list(getattr(result, "hits", []) or [])
        outcome = Outcome(item=item,
                          ranked=[getattr(hit, "url", "") or "" for hit in hits],
                          ranks=[int(getattr(hit, "chunk_id", 0)) for hit in hits],
                          passage_hit=passage_hit(
                              item, [getattr(hit, "text", "") for hit in hits]))
        if with_answers:
            from .answer import ask
            answer = ask(store, embedder, item.question, cfg, top_k=top_k)
            outcome.answer = getattr(answer, "text", "") or ""
            outcome.cited = [str(citation.get("url", ""))
                             for citation in (getattr(answer, "citations", []) or [])
                             if isinstance(citation, dict)]
        outcome.seconds = time.perf_counter() - started
        outcomes.append(outcome)
    return outcomes
