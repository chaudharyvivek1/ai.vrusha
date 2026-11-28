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


# ---------------------------------------------------------------------------
# Option 3: a multi-step goal
# ---------------------------------------------------------------------------


@dataclass
class MultiStep:
    """A goal where step 2 needs step 1's output.

    `docs/PLAN-V3.md` Part 35 measured the hierarchy's **cost** (42 board entries,
    identical quality) but never its **benefit**, because documentation has no
    second step. This does: research three companies, then compare them, and the
    comparison can only use what the research produced.
    """

    companies: List[str] = field(default_factory=list)
    single_seconds: float = 0.0
    orchestrated_seconds: float = 0.0
    single_facts: int = 0
    orchestrated_facts: int = 0
    board_entries: int = 0
    tasks: int = 0
    orchestrated_context: int = 0     # characters the compare step was given
    single_context: int = 0

    def to_dict(self) -> dict:
        return {"companies": len(self.companies),
                "single_seconds": round(self.single_seconds, 2),
                "orchestrated_seconds": round(self.orchestrated_seconds, 2),
                "single_facts": self.single_facts,
                "orchestrated_facts": self.orchestrated_facts,
                "board_entries": self.board_entries,
                "tasks": self.tasks,
                "single_context": self.single_context,
                "orchestrated_context": self.orchestrated_context}


def multistep(store, embedder, cfg: dict, board, claims, registry,
              companies: Sequence[str], *, workers: int = 3,
              run_id: str = "multistep", shape: str = "stages") -> MultiStep:
    """Research N companies, then compare them -- both ways."""
    from . import orchestrator as orch
    from .store.query import retrieve

    companies = list(companies)
    result = MultiStep(companies=companies)

    def research(name: str, top_k: int = 3, on=None) -> str:
        # `on` is the thread's own connection: a SQLite connection is bound to the
        # thread that made it, so a worker must never reuse the caller's store
        found = retrieve(on or store, embedder, f"{name} business", cfg, top_k=top_k)
        return "\n".join(hit.text for hit in found.hits)

    # 1. one agent: every company's raw text goes into one context, then compare
    started = time.perf_counter()
    single_chunks = [research(name) for name in companies]
    single_context = "\n".join(single_chunks)
    result.single_context = len(single_context)
    result.single_facts = sum(1 for name in companies
                              if name.split()[0].lower() in single_context.lower())
    result.single_seconds = time.perf_counter() - started

    # 2. the hierarchy: one task per company, then a compare task that may only
    #    use the findings the research tasks left on the board
    started = time.perf_counter()

    def worker(task: str, wstore=None) -> str:
        if task.startswith("compare"):
            # step 2 reads step 1's output -- this is the whole point
            findings = [entry.summary for entry in board.since(run_id, 0, limit=200)
                        if entry.kind == "finding"]
            result.orchestrated_context = sum(len(text) for text in findings)
            mentioned = sum(1 for name in companies
                            if name.split()[0].lower()
                            in " ".join(findings).lower())
            return f"compared {mentioned} of {len(companies)} companies"
        # the finding carries the entity name, so the metric compares like with
        # like: the single run counts names in 22,970 characters of raw text, and
        # this counts names in a finding that *says* which company it is about
        return f"{task.removeprefix('research ')}: " + research(
            task.removeprefix("research "), on=wstore)[:300]

    # The compare task needs the research to exist first. `stages` expresses that
    # as two linear waves; `graph` expresses it as a dependency, which is the same
    # plan written the general way. Both must produce the same answer -- if they do
    # not, one of the two shapes is wrong.
    if shape == "graph":
        tasks = [(f"research {name}", []) for name in companies]
        tasks.append(("compare the findings",
                      [f"research {name}" for name in companies]))
        run_result = orch.run_graph(
            store, board, claims, registry, cfg, "research and compare",
            tasks=tasks, worker=worker, workers=workers, run_id=run_id,
            store_factory=getattr(store, "_factory", None))
    elif shape == "stages":
        run_result = orch.run_stages(
            store, board, claims, registry, cfg, "research and compare",
            stages=[[f"research {name}" for name in companies],
                    ["compare the findings"]],
            worker=worker, workers=workers, run_id=run_id,
            store_factory=getattr(store, "_factory", None))
    else:
        raise ValueError(f"unknown shape {shape!r}; expected 'stages' or 'graph'")
    result.orchestrated_seconds = time.perf_counter() - started
    result.tasks = len(run_result.tasks)
    result.board_entries = run_result.entries
    result.orchestrated_facts = sum(
        1 for name in companies
        if name.split()[0].lower() in " ".join(
            entry.summary for entry in board.since(run_id, 0, limit=200)
            if entry.kind == "finding").lower())
    return result
