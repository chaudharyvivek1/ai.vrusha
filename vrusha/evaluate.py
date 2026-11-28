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

import json
import math
import os
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
    #: characters of retrieved context, and characters of answer. Tokens are
    #: estimated from these by `estimate_tokens`; a real tokeniser would be more
    #: exact and would also make the measurement depend on which tokeniser is
    #: installed, which is worse for comparing runs.
    context_chars: int = 0
    answer_chars: int = 0
    model_calls: int = 0
    answered: bool = False

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
# cost
# ---------------------------------------------------------------------------

#: Characters per token, the usual rough ratio for English prose. Labelled an
#: estimate everywhere it is reported: a real tokeniser would be exact and would
#: also make two runs incomparable when different tokenisers are installed.
CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    return int(math.ceil(len(text or "") / CHARS_PER_TOKEN))


@dataclass
class Cost:
    """What one question cost, in the three currencies that matter."""

    context_tokens: int = 0
    answer_tokens: int = 0
    seconds: float = 0.0
    model_calls: int = 0
    usd: float = 0.0

    def to_dict(self) -> dict:
        return {"context_tokens": self.context_tokens,
                "answer_tokens": self.answer_tokens,
                "seconds": round(self.seconds, 3),
                "model_calls": self.model_calls,
                "usd": round(self.usd, 6)}


def cost_of(outcome: Outcome, *, price_in: float = 0.0,
            price_out: float = 0.0) -> Cost:
    """Tokens, seconds and money for one question.

    `price_in` and `price_out` are currency per 1,000 tokens. They default to
    zero because the default answerer is local and extractive: reporting a made-up
    price for a model nobody called would be the worst kind of number.
    """
    context = estimate_tokens("x" * outcome.context_chars)
    answer = estimate_tokens("x" * outcome.answer_chars)
    usd = (context / 1000.0) * float(price_in) + (answer / 1000.0) * float(price_out)
    return Cost(context_tokens=context, answer_tokens=answer,
                seconds=outcome.seconds, model_calls=outcome.model_calls,
                usd=usd)


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def summarise(outcomes: Sequence[Outcome], *,
              price_in: float = 0.0,
              price_out: float = 0.0) -> Dict[str, float]:
    """One row of the table: every metric, averaged over the questions."""
    if not outcomes:
        return {}
    count = float(len(outcomes))

    def mean(values: List[float]) -> float:
        return sum(values) / count if values else 0.0

    citations = [value for value in
                 (citation_accuracy(outcome) for outcome in outcomes)
                 if value is not None]
    costs = [cost_of(outcome, price_in=price_in, price_out=price_out)
             for outcome in outcomes]
    answered = [outcome for outcome in outcomes if outcome.answered]

    def mean_cost(attribute: str, rows) -> float:
        return (sum(getattr(row, attribute) for row in rows) / len(rows)) if rows else 0.0

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
        # cost, per question and per *answered* question: a run that refuses
        # half its questions is cheap per question and expensive per answer
        "answered": len(answered),
        "context_tokens": mean_cost("context_tokens", costs),
        "answer_tokens": mean_cost("answer_tokens", costs),
        "usd_per_question": mean_cost("usd", costs),
        "usd_per_answered": (sum(cost.usd for cost in costs) / len(answered)
                             if answered else 0.0),
        "seconds_per_answered": (sum(o.seconds for o in answered) / len(answered)
                                 if answered else 0.0),
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
    if "context_tokens" in summary:
        lines.append("")
        lines.append("cost")
        lines.append("-" * 78)
        lines.append(f"{'answered':<18}{int(summary.get('answered', 0)):>9}"
                     f"  questions with an answer, of {int(summary['questions'])}")
        lines.append(f"{'context tokens':<18}{summary['context_tokens']:>9.0f}"
                     f"  retrieved text, estimated at {CHARS_PER_TOKEN:g} chars/token")
        lines.append(f"{'answer tokens':<18}{summary['answer_tokens']:>9.0f}"
                     f"  the answer itself")
        lines.append(f"{'seconds/answered':<18}{summary['seconds_per_answered']:>9.2f}"
                     f"  wall clock for a question that got an answer")
        if summary.get("usd_per_question"):
            lines.append(f"{'cost/question':<18}{summary['usd_per_question']:>9.4f}"
                         f"  at the configured price per 1k tokens")
        else:
            lines.append(f"{'cost/question':<18}{'local':>9}"
                         f"  no model was called, so there is no token price")
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
        *, top_k: int = 5, with_answers: bool = False,
        reranker=None, rerank_off: bool = False) -> List[Outcome]:
    """Ask every question, and record where the right answer came from.

    Retrieval is measured on every question; the answer and its citations only
    when `with_answers` is set, because composing an answer costs a model call
    and the retrieval numbers do not depend on it.
    """
    import time

    from .store.query import retrieve

    # An empty store scores 0% on every metric, which reads as "retrieval is
    # broken" rather than "there is nothing to retrieve". Say which it is.
    chunks = store.conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE active = 1").fetchone()[0]
    if not chunks:
        raise RuntimeError(
            "the store has no chunks, so there is nothing to measure: "
            "`vrusha ingest --dir <path> --licence MIT` first")

    outcomes: List[Outcome] = []
    for item in items:
        started = time.perf_counter()
        result = retrieve(store, embedder, item.question, cfg, top_k=top_k,
                          reranker=reranker, rerank_off=rerank_off)
        hits = list(getattr(result, "hits", []) or [])
        outcome = Outcome(item=item,
                          ranked=[getattr(hit, "url", "") or "" for hit in hits],
                          ranks=[int(getattr(hit, "chunk_id", 0)) for hit in hits],
                          passage_hit=passage_hit(
                              item, [getattr(hit, "text", "") for hit in hits]),
                          context_chars=sum(len(getattr(hit, "text", "") or "")
                                            for hit in hits))
        if with_answers:
            from .answer import ask
            answer = ask(store, embedder, item.question, cfg, top_k=top_k)
            outcome.answer = getattr(answer, "text", "") or ""
            outcome.answer_chars = len(outcome.answer)
            outcome.answered = bool(outcome.answer)
            outcome.model_calls = 1 if getattr(answer, "method", "") not in (
                "", "extractive") else 0
            outcome.cited = [str(citation.get("url", ""))
                             for citation in (getattr(answer, "citations", []) or [])
                             if isinstance(citation, dict)]
        outcome.seconds = time.perf_counter() - started
        outcomes.append(outcome)
    return outcomes


# ---------------------------------------------------------------------------
# sets on disk, and the loader that refuses a malformed one
# ---------------------------------------------------------------------------

SETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_sets")

#: Named sets that ship with the package.
SETS: Dict[str, str] = {
    "docs": os.path.join(SETS_DIR, "docs-40.json"),
}


class MalformedSet(ValueError):
    """A set that would measure the wrong thing if it were loaded quietly."""


def load_set(path_or_name: str) -> List[Item]:
    """Load a labelled set, refusing anything that is not fully labelled.

    A benchmark with an unlabelled question does not fail — it scores that
    question as a miss, and the number quietly becomes a measure of the labels
    rather than of the system. So every question must have a document and at
    least one keyword, and a duplicate question is refused too: it would be
    counted twice and weight itself.
    """
    path = SETS.get(path_or_name, path_or_name)
    if not os.path.exists(path):
        raise MalformedSet(
            f"no such set {path_or_name!r}; known sets: {', '.join(sorted(SETS))}")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except ValueError as exc:
        raise MalformedSet(f"{path}: not valid JSON ({exc})") from exc

    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list) or not raw_items:
        raise MalformedSet(f"{path}: expected a non-empty list of items")

    items: List[Item] = []
    seen: Dict[str, int] = {}
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise MalformedSet(f"{path}: item {index} is not an object")
        question = str(raw.get("question") or "").strip()
        document = str(raw.get("document") or "").strip()
        keywords = raw.get("keywords") or []
        if not question:
            raise MalformedSet(f"{path}: item {index} has no question")
        if not document:
            raise MalformedSet(f"{path}: item {index} ({question!r}) has no document")
        if not isinstance(keywords, list) or not keywords:
            raise MalformedSet(
                f"{path}: item {index} ({question!r}) has no keywords, so its "
                f"passage can never be found")
        if not all(isinstance(word, str) and word.strip() for word in keywords):
            raise MalformedSet(f"{path}: item {index} has a blank keyword")
        if question in seen:
            raise MalformedSet(
                f"{path}: item {index} repeats item {seen[question]} "
                f"({question!r}); a duplicate is counted twice")
        seen[question] = index
        items.append(Item(question=question, document=document,
                          keywords=tuple(word.strip() for word in keywords),
                          note=str(raw.get("note") or "")))
    return items
