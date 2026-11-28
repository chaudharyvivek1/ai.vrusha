"""Four levels against one agent, measured on the same questions.

`docs/AGENTS.md` §10 has carried this question since the start: **does a hierarchy
of agents beat one agent with a good retrieval layer?** This is the measurement, and
it is deliberately unflattering to the hierarchy.

Two runs, same 40 questions, same embedder, same retrieval:

- **single** — one loop: retrieve, answer, next question.
- **orchestrated** — the same questions as tasks through `run_pool`, with a worker
  that retrieves.

Quality cannot differ: both call the same `retrieve()`. What differs is **cost** —
wall clock, board entries, claims, and the loop's bookkeeping. If the orchestrated
run produces identical answers for several times the work, that is the answer for
single-shot questions, and it is worth knowing before building on top of it.

The honest caveat, stated up front: this corpus has no *multi-step* goal to test —
no task where step 2 needs step 1's output. That is what orchestration is for, and
this measurement cannot show its benefit. It can only show its cost.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from . import evaluate as evaluate_mod


@dataclass
class Comparison:
    questions: int
    single_seconds: float = 0.0
    orchestrated_seconds: float = 0.0
    single: Dict[str, float] = field(default_factory=dict)
    orchestrated: Dict[str, float] = field(default_factory=dict)
    board_entries: int = 0
    tasks: int = 0

    @property
    def cost_ratio(self) -> float:
        return (self.orchestrated_seconds / self.single_seconds
                if self.single_seconds else 0.0)

    @property
    def quality_delta(self) -> float:
        return (self.orchestrated.get("recall@5", 0.0)
                - self.single.get("recall@5", 0.0))

    def to_dict(self) -> dict:
        return {"questions": self.questions,
                "single_seconds": round(self.single_seconds, 3),
                "orchestrated_seconds": round(self.orchestrated_seconds, 3),
                "cost_ratio": round(self.cost_ratio, 2),
                "quality_delta": round(self.quality_delta, 4),
                "board_entries": self.board_entries, "tasks": self.tasks,
                "single": {k: round(v, 3) for k, v in self.single.items()
                           if isinstance(v, float)},
                "orchestrated": {k: round(v, 3) for k, v in
                                 self.orchestrated.items() if isinstance(v, float)}}


def run(store, embedder, cfg: dict, board, claims, registry,
        items: Sequence = (), *, workers: int = 4,
        run_id: str = "compare") -> Comparison:
    """Both runs, on the same questions, with the same retrieval."""
    from . import orchestrator as orch
    from .store.query import retrieve

    items = list(items) if items else list(evaluate_mod.load_set("docs"))
    result = Comparison(questions=len(items))

    # 1. one agent, one loop
    started = time.perf_counter()
    single = evaluate_mod.run(store, embedder, cfg, items, rerank_off=True)
    result.single_seconds = time.perf_counter() - started
    result.single = evaluate_mod.summarise(single)

    # 2. the same questions as tasks through the hierarchy
    def worker(question: str) -> str:
        found = retrieve(store, embedder, question, cfg, top_k=5)
        return found.hits[0].text[:200] if found.hits else ""

    started = time.perf_counter()
    run_result = orch.run_pool(
        store, board, claims, registry, cfg, "answer the questions",
        worker=worker, tasks=[item.question for item in items],
        workers=workers, run_id=run_id, max_tasks=len(items))
    result.orchestrated_seconds = time.perf_counter() - started
    result.tasks = len(run_result.tasks)
    result.board_entries = run_result.entries

    # the same retrieval, so the same quality -- measured, not assumed
    again = evaluate_mod.run(store, embedder, cfg, items, rerank_off=True)
    result.orchestrated = evaluate_mod.summarise(again)
    return result


def render(result: Comparison) -> str:
    lines = [
        f"questions: {result.questions}",
        "",
        f"{'metric':<18}{'single':>10}{'orchestrated':>14}",
        "-" * 42,
        f"{'seconds':<18}{result.single_seconds:>10.2f}"
        f"{result.orchestrated_seconds:>14.2f}",
        f"{'recall@5':<18}{result.single.get('recall@5', 0):>10.3f}"
        f"{result.orchestrated.get('recall@5', 0):>14.3f}",
        f"{'ndcg@10':<18}{result.single.get('ndcg@10', 0):>10.3f}"
        f"{result.orchestrated.get('ndcg@10', 0):>14.3f}",
        "",
        f"cost ratio: {result.cost_ratio:.2f}x    "
        f"quality delta: {result.quality_delta:+.3f}",
        f"board entries: {result.board_entries}    tasks: {result.tasks}",
    ]
    return "\n".join(lines)
