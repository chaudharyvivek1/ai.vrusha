"""BEIR-format sets: someone else's labels, not ours.

`docs/PLAN-V3.md` deferred this from Phase 0, and it is the only evaluation here
that does not grade its own homework: the queries, the corpus and the relevance
judgements all come from outside.

**Datasets, not leaderboards.** `MASTER-PLAN.md` refused to chase a published score,
because the published suites are saturated or contaminated. That refusal does not
extend to *data*: a BEIR set is queries and judgements, and running it says whether
this retriever works on someone else's corpus. The score is ours to publish or not.

Format, as BEIR defines it::

    corpus.jsonl     {"_id", "title", "text"}
    queries.jsonl    {"_id", "text"}
    qrels/test.tsv   query-id <TAB> corpus-id <TAB> score

**What is not measured here:** a real BEIR download. `huggingface.co` answers, but
the dataset file endpoints do not from this sandbox, so the loader is tested against
a bundled five-document fixture and the work log says so. The format is the part
that has to be right; the bytes are a download away.
"""
from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


class BeirError(ValueError):
    """A set that cannot be read is refused, with the line number."""


@dataclass
class BeirSet:
    name: str
    corpus: List[dict] = field(default_factory=list)
    queries: List[dict] = field(default_factory=list)
    qrels: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @property
    def documents(self) -> int:
        return len(self.corpus)

    @property
    def judgements(self) -> int:
        return sum(len(rows) for rows in self.qrels.values())

    def to_dict(self) -> dict:
        return {"name": self.name, "documents": self.documents,
                "queries": len(self.queries), "judgements": self.judgements}


def _open(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def read_jsonl(path: str) -> List[dict]:
    rows = []
    with _open(path) as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise BeirError(f"{os.path.basename(path)}:{number}: {exc}") from exc
    return rows


def read_qrels(path: str) -> Dict[str, Dict[str, int]]:
    """`query-id <TAB> corpus-id <TAB> score`, with a header line."""
    qrels: Dict[str, Dict[str, int]] = {}
    with _open(path) as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                if number == 1:
                    continue                 # the header
                raise BeirError(f"{os.path.basename(path)}:{number}: expected "
                                f"three tab-separated columns, got {len(parts)}")
            query, document, score = parts[0], parts[1], parts[2]
            if number == 1 and not score.strip().lstrip("-").isdigit():
                continue                     # header written as words
            try:
                value = int(float(score))
            except ValueError as exc:
                raise BeirError(f"{os.path.basename(path)}:{number}: "
                                f"score {score!r} is not a number") from exc
            if value > 0:
                qrels.setdefault(query, {})[document] = value
    return qrels


def load(directory: str, *, name: str = "") -> BeirSet:
    """A directory holding corpus.jsonl, queries.jsonl and qrels/test.tsv."""
    corpus_path = os.path.join(directory, "corpus.jsonl")
    queries_path = os.path.join(directory, "queries.jsonl")
    qrels_path = os.path.join(directory, "qrels", "test.tsv")
    for path in (corpus_path, queries_path, qrels_path):
        if not os.path.exists(path) and not os.path.exists(path + ".gz"):
            raise BeirError(f"{path} is missing; a BEIR set needs corpus.jsonl, "
                            f"queries.jsonl and qrels/test.tsv")
    corpus = read_jsonl(corpus_path if os.path.exists(corpus_path)
                        else corpus_path + ".gz")
    queries = read_jsonl(queries_path if os.path.exists(queries_path)
                         else queries_path + ".gz")
    qrels = read_qrels(qrels_path if os.path.exists(qrels_path)
                       else qrels_path + ".gz")
    if not corpus or not queries:
        raise BeirError(f"{directory}: the corpus or the queries are empty")
    return BeirSet(name=name or os.path.basename(directory.rstrip("/")),
                   corpus=corpus, queries=queries, qrels=qrels)


def bundled() -> BeirSet:
    """The five-document fixture that ships with the package."""
    return load(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "beir-mini"), name="beir-mini")


def score(ranked: Sequence[str], relevant: Dict[str, int], k: int) -> dict:
    """Recall@k, precision@k, MRR and nDCG@k for one query, from BEIR qrels."""
    import math

    top = list(ranked)[:k]
    hits = [doc for doc in top if doc in relevant]
    recall = len(hits) / len(relevant) if relevant else 0.0
    precision = len(hits) / k if k else 0.0
    rank = next((index + 1 for index, doc in enumerate(top) if doc in relevant), 0)
    reciprocal = 1.0 / rank if rank else 0.0
    dcg = sum(1.0 / math.log2(index + 2)
              for index, doc in enumerate(top) if doc in relevant)
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(relevant), k)))
    return {"recall": recall, "precision": precision, "mrr": reciprocal,
            "ndcg": (dcg / ideal) if ideal else 0.0}
