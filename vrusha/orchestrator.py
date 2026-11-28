"""The loop: L1 plans, L2 decomposes, L3 works, L4 reports.

Every piece existed and none of them ran together: `blackboard` records, `claims`
arbitrate, `registry` counts slots, `compaction` folds, `anticipate` warms. This is
the loop that uses them in that order, and it is deliberately small — the parts do
the work, and the orchestrator's only job is to call them correctly.

Three rules, learned from the parts:

**The worker is injected.** `run(..., worker=callable)` means the loop can be tested
without a model, a network or a query — and it means the same loop drives a fake
worker in a test and a real retrieval in production.

**A failing task is retried once, then marked failed.** Retrying forever hides a
broken worker; not retrying wastes a whole run on one flaky call.

**Every task ends in a state.** Done or failed, on the board and in the claims, so a
run that dies leaves a record of where it died rather than a silent gap.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

#: One retry: enough for a flaky call, not enough to hide a broken worker.
MAX_ATTEMPTS = 2


@dataclass
class Task:
    name: str
    state: str = "pending"        # pending | done | failed
    attempts: int = 0
    result: str = ""
    error: str = ""


@dataclass
class Run:
    goal: str
    tasks: List[Task] = field(default_factory=list)
    seconds: float = 0.0
    entries: int = 0

    @property
    def done(self) -> int:
        return sum(1 for task in self.tasks if task.state == "done")

    @property
    def failed(self) -> int:
        return sum(1 for task in self.tasks if task.state == "failed")

    def to_dict(self) -> dict:
        return {"goal": self.goal, "tasks": len(self.tasks),
                "done": self.done, "failed": self.failed,
                "seconds": round(self.seconds, 3), "entries": self.entries}


def decompose(goal: str, *, limit: int = 8) -> List[str]:
    """Split a goal into task names.

    Rule-based and honest: it splits on `then`, `and then`, semicolons and newlines,
    and otherwise returns the goal as one task. A cleverer decomposition needs a
    model and a measurement, and neither exists yet — `docs/AGENTS.md` §10 lists
    this as the open question.
    """
    text = (goal or "").strip()
    if not text:
        return []
    for separator in (" then ", " and then ", ";", "\n"):
        if separator in text:
            parts = [part.strip(" .") for part in text.split(separator)]
            parts = [part for part in parts if part]
            if len(parts) > 1:
                return parts[:limit]
    return [text[:200]]


def run(store, board, claims, registry, cfg, goal: str, *,
        worker: Callable[[str], str], tasks: Optional[Sequence[str]] = None,
        run_id: str = "", max_tasks: int = 64) -> Run:
    """Plan, claim, work, report — one pass, bounded, with every state recorded."""
    started = time.perf_counter()
    # `int(started)` collides for every run in the same second, so a second run
    # found the first one's claims already `done` and every task failed silently.
    # Found by a benchmark that ran the loop four times in a row.
    run_id = run_id or f"run-{int(started * 1000)}-{uuid.uuid4().hex[:6]}"
    names = list(tasks) if tasks is not None else decompose(goal)
    names = names[:max_tasks]
    result = Run(goal=goal, tasks=[Task(name=name) for name in names])
    if not names:
        return result

    registry.open_run(run_id)
    super_slot = registry.register(run_id, "L1/super", level="L1", skill="plan")
    admin = registry.register(run_id, "L2/admin", level="L2", skill="decompose")
    board.append(run_id, task="plan", agent="L1/super", kind="decision",
                 summary=f"goal: {goal[:160]} ({len(names)} task(s))")
    if not (super_slot.registered and admin.registered):
        result.seconds = time.perf_counter() - started
        return result

    try:
        for index, task in enumerate(result.tasks):
            worker_name = f"L3/worker-{index % 8}"
            registry.register(run_id, worker_name, level="L3", skill="work")
            granted = claims.claim(run_id, task.name, worker_name, ttl=120)
            if not granted.granted:
                task.state = "failed"
                task.error = f"not claimed: {granted.reason}"
                board.append(run_id, task=task.name, agent=worker_name,
                             kind="error", summary=task.error)
                continue
            while task.attempts < MAX_ATTEMPTS:
                task.attempts += 1
                try:
                    task.result = worker(task.name) or ""
                    task.state = "done"
                    claims.complete(run_id, task.name, worker_name, status="done")
                    board.append(run_id, task=task.name, agent=worker_name,
                                 kind="finding", summary=task.result[:400] or "(empty)")
                    break
                except Exception as exc:         # noqa: BLE001 - one retry, then failed
                    task.error = f"{type(exc).__name__}: {exc}"
                    if task.attempts >= MAX_ATTEMPTS:
                        task.state = "failed"
                        claims.complete(run_id, task.name, worker_name,
                                        status="failed")
                        board.append(run_id, task=task.name, agent=worker_name,
                                     kind="error", summary=task.error[:400])
            registry.release(run_id, worker_name)
    finally:
        board.append(run_id, task="report", agent="L2/admin", kind="artifact",
                     summary=f"{result.done} done, {result.failed} failed "
                             f"of {len(result.tasks)}",
                     refs=[f"run:{run_id}"])
        registry.release(run_id, "L1/super")
        registry.release(run_id, "L2/admin")
        result.entries = len(board.since(run_id, 0, limit=1000))
        result.seconds = time.perf_counter() - started
    return result


def render(result: Run) -> str:
    lines = [f"goal: {result.goal[:70]}",
             f"  {result.done} done, {result.failed} failed of {len(result.tasks)}"
             f" in {result.seconds:.2f}s, {result.entries} board entries"]
    for task in result.tasks:
        mark = {"done": "ok  ", "failed": "FAIL", "pending": "    "}[task.state]
        detail = (task.result or task.error or "")[:56]
        lines.append(f"  {mark} {task.name[:40]:<42}{detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Part 32: the bounded pool
# ---------------------------------------------------------------------------

#: The pool size. `docs/AGENTS.md` §4 says 64 logical agents over a small pool;
#: this is the pool, and the benchmark below says what it is worth.
DEFAULT_WORKERS = 4


def run_pool(store, board, claims, registry, cfg, goal: str, *,
             worker: Callable[[str], str], tasks: Optional[Sequence[str]] = None,
             workers: int = DEFAULT_WORKERS, run_id: str = "",
             max_tasks: int = 64) -> Run:
    """The same loop, with the tasks spread over a bounded pool.

    Every board write takes one lock, so a pool does not make the *bookkeeping*
    faster — it makes the *worker* overlap, which only helps if the worker waits on
    something. The benchmark measures both, because "parallel is faster" is an
    assumption that a single-writer store can quietly falsify.
    """
    from concurrent.futures import ThreadPoolExecutor

    started = time.perf_counter()
    run_id = run_id or f"run-{int(started * 1000)}-{uuid.uuid4().hex[:6]}"
    names = (list(tasks) if tasks is not None else decompose(goal))[:max_tasks]
    result = Run(goal=goal, tasks=[Task(name=name) for name in names])
    if not names:
        return result

    registry.open_run(run_id)
    registry.register(run_id, "L1/super", level="L1", skill="plan")
    registry.register(run_id, "L2/admin", level="L2", skill="decompose")
    board.append(run_id, task="plan", agent="L1/super", kind="decision",
                 summary=f"goal: {goal[:160]} ({len(names)} task(s), "
                         f"{workers} worker(s))")

    def work(index: int) -> None:
        task = result.tasks[index]
        name = f"L3/worker-{index % max(1, workers)}"
        registry.register(run_id, name, level="L3", skill="work")
        granted = claims.claim(run_id, task.name, name, ttl=120)
        if not granted.granted:
            task.state, task.error = "failed", f"not claimed: {granted.reason}"
            board.append(run_id, task=task.name, agent=name, kind="error",
                         summary=task.error)
            return
        try:
            while task.attempts < MAX_ATTEMPTS:
                task.attempts += 1
                try:
                    task.result = worker(task.name) or ""
                    task.state = "done"
                    claims.complete(run_id, task.name, name, status="done")
                    board.append(run_id, task=task.name, agent=name, kind="finding",
                                 summary=task.result[:400] or "(empty)")
                    return
                except Exception as exc:         # noqa: BLE001
                    task.error = f"{type(exc).__name__}: {exc}"
                    if task.attempts >= MAX_ATTEMPTS:
                        task.state = "failed"
                        claims.complete(run_id, task.name, name, status="failed")
                        board.append(run_id, task=task.name, agent=name,
                                     kind="error", summary=task.error[:400])
        finally:
            registry.release(run_id, name)

    try:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            list(pool.map(work, range(len(result.tasks))))
    finally:
        board.append(run_id, task="report", agent="L2/admin", kind="artifact",
                     summary=f"{result.done} done, {result.failed} failed "
                             f"of {len(result.tasks)} ({workers} worker(s))",
                     refs=[f"run:{run_id}"])
        registry.release(run_id, "L1/super")
        registry.release(run_id, "L2/admin")
        result.entries = len(board.since(run_id, 0, limit=1000))
        result.seconds = time.perf_counter() - started
    return result


# ---------------------------------------------------------------------------
# Part 34: the loop over HTTP
# ---------------------------------------------------------------------------


def run_remote(store, board, claims, registry, cfg, goal: str, *,
               worker: Callable[[str], str], base_url: str,
               tasks: Optional[Sequence[str]] = None,
               workers: int = DEFAULT_WORKERS, run_id: str = "",
               max_tasks: int = 64) -> Run:
    """The same loop, with every board and claim call going over HTTP.

    This is the shape an L4 agent on another machine takes: it claims from the
    server, does the work locally, reports back. The local `board`/`claims` objects
    are used only for the final count, so a caller can pass the server's own.

    **What it costs, measured:** each task now pays three round trips — claim,
    append, complete — where the in-process loop paid none. `bench/registry.py`
    reports both, because "remote is slower" is not a number.
    """
    from concurrent.futures import ThreadPoolExecutor

    from .remote import RemoteBoard

    started = time.perf_counter()
    run_id = run_id or f"run-{int(started * 1000)}-{uuid.uuid4().hex[:6]}"
    names = (list(tasks) if tasks is not None else decompose(goal))[:max_tasks]
    result = Run(goal=goal, tasks=[Task(name=name) for name in names])
    if not names:
        return result

    remote = RemoteBoard(base_url)
    remote.append(run_id, task="plan", agent="L1/super", kind="decision",
                  summary=f"goal: {goal[:160]} ({len(names)} task(s), remote)")

    def work(index: int) -> None:
        task = result.tasks[index]
        name = f"L4/remote-{index % max(1, workers)}"
        granted = remote.claim(run_id, task.name, name, ttl=120)
        if not granted.get("granted"):
            task.state = "failed"
            task.error = f"not claimed: {granted.get('reason', '')}"
            remote.append(run_id, task=task.name, agent=name, kind="error",
                          summary=task.error)
            return
        while task.attempts < MAX_ATTEMPTS:
            task.attempts += 1
            try:
                task.result = worker(task.name) or ""
                task.state = "done"
                remote.complete(run_id, task.name, name, status="done")
                remote.append(run_id, task=task.name, agent=name, kind="finding",
                              summary=task.result[:400] or "(empty)")
                return
            except Exception as exc:             # noqa: BLE001
                task.error = f"{type(exc).__name__}: {exc}"
                if task.attempts >= MAX_ATTEMPTS:
                    task.state = "failed"
                    remote.complete(run_id, task.name, name, status="failed")
                    remote.append(run_id, task=task.name, agent=name, kind="error",
                                  summary=task.error[:400])

    try:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            list(pool.map(work, range(len(result.tasks))))
    finally:
        remote.append(run_id, task="report", agent="L2/admin", kind="artifact",
                      summary=f"{result.done} done, {result.failed} failed "
                              f"of {len(result.tasks)} (remote, {workers} worker(s))",
                      refs=[f"run:{run_id}"])
        result.entries = len(board.since(run_id, 0, limit=1000))
        result.seconds = time.perf_counter() - started
    return result


def run_stages(store, board, claims, registry, cfg, goal: str, *,
               stages: Sequence[Sequence[str]],
               worker: Callable[[str], str], workers: int = DEFAULT_WORKERS,
               run_id: str = "", store_factory: Optional[Callable] = None,
               skip_if: Optional[Callable] = None) -> Run:
    """Tasks in **waves**: every task in a stage finishes before the next begins.

    `run_pool` maps a flat list concurrently, so a task that needs another task's
    output races it. Found by the multi-step comparison: the `compare` task read the
    board before any research had written to it and reported **0 of 3 companies** --
    a race, not a quality problem.

    A stage is the smallest ordering primitive that fixes it, and it costs nothing
    when there is only one stage.

    **`store_factory`** exists because a worker runs in a thread and a SQLite
    connection is bound to the thread that made it. Without it, any worker that
    touches the store raises `ProgrammingError: SQLite objects created in a thread
    can only be used in that same thread` -- found by diagnostic A, and the reason
    every research task failed while the compare task succeeded.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    started = time.perf_counter()
    run_id = run_id or f"run-{int(started * 1000)}-{uuid.uuid4().hex[:6]}"
    names = [name for stage in stages for name in stage]
    result = Run(goal=goal, tasks=[Task(name=name) for name in names])
    if not names:
        return result

    registry.open_run(run_id)
    registry.register(run_id, "L1/super", level="L1", skill="plan")
    board.append(run_id, task="plan", agent="L1/super", kind="decision",
                 summary=f"goal: {goal[:160]} ({len(stages)} stage(s), "
                         f"{len(names)} task(s))")

    local = threading.local()

    def _worker_store():
        """One connection per thread, opened on first use and closed by the caller."""
        if store_factory is None:
            return store
        if getattr(local, "store", None) is None:
            local.store = store_factory()
        return local.store

    def work(index: int) -> None:
        task = result.tasks[index]
        name = f"L3/worker-{index % max(1, workers)}"
        # A task whose dependencies did not succeed must not run at all: running it
        # on missing input and failing it afterwards is not a barrier.
        if skip_if is not None:
            reason = skip_if(task.name, result)
            if reason:
                task.state, task.error = "failed", reason
                board.append(run_id, task=task.name, agent="L2/admin",
                             kind="error", summary=reason[:400])
                return
        registry.register(run_id, name, level="L3", skill="work")
        granted = claims.claim(run_id, task.name, name, ttl=120)
        if not granted.granted:
            task.state, task.error = "failed", f"not claimed: {granted.reason}"
            return
        try:
            while task.attempts < MAX_ATTEMPTS:
                task.attempts += 1
                try:
                    task.result = worker(task.name, _worker_store()) \
                        if store_factory is not None else worker(task.name)
                    task.result = task.result or ""
                    task.state = "done"
                    claims.complete(run_id, task.name, name, status="done")
                    board.append(run_id, task=task.name, agent=name, kind="finding",
                                 summary=task.result[:400] or "(empty)")
                    return
                except Exception as exc:         # noqa: BLE001
                    task.error = f"{type(exc).__name__}: {exc}"
                    if task.attempts >= MAX_ATTEMPTS:
                        task.state = "failed"
                        claims.complete(run_id, task.name, name, status="failed")
                        board.append(run_id, task=task.name, agent=name,
                                     kind="error", summary=task.error[:400])
        finally:
            registry.release(run_id, name)

    try:
        offset = 0
        for stage in stages:
            with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
                list(pool.map(work, range(offset, offset + len(stage))))
            offset += len(stage)
    finally:
        board.append(run_id, task="report", agent="L2/admin", kind="artifact",
                     summary=f"{result.done} done, {result.failed} failed of "
                             f"{len(result.tasks)} ({len(stages)} stage(s))",
                     refs=[f"run:{run_id}"])
        registry.release(run_id, "L1/super")
        result.entries = len(board.since(run_id, 0, limit=1000))
        result.seconds = time.perf_counter() - started
    return result


# ---------------------------------------------------------------------------
# The second shape: a dependency graph
# ---------------------------------------------------------------------------


class CycleError(ValueError):
    """A task graph with a cycle cannot be scheduled, and says where."""


def levels(tasks: Sequence[tuple]) -> List[List[str]]:
    """`[(name, [after...])]` -> the waves a scheduler can run.

    `run_stages` orders tasks linearly, which is one shape of dependency. This is
    the general one: a task names what it waits for, and the waves fall out of the
    graph. `B` and `C` both depending on `A` land in the same wave; `D` depending on
    both waits for both.

    Raises `CycleError` with the cycle, because a cycle is a mistake in the caller's
    plan and running it partially would hide it.
    """
    names = [name for name, _ in tasks]
    if len(set(names)) != len(names):
        seen, duplicates = set(), set()
        for name in names:
            (duplicates if name in seen else seen).add(name)
        raise ValueError(f"duplicate task name(s): {', '.join(sorted(duplicates))}")

    after: Dict[str, List[str]] = {}
    for name, dependencies in tasks:
        after[name] = list(dependencies or [])
        for dependency in after[name]:
            if dependency not in names:
                raise ValueError(f"{name!r} depends on {dependency!r}, "
                                 f"which is not in the graph")

    waves: List[List[str]] = []
    done: set = set()
    remaining = dict(after)
    while remaining:
        wave = sorted(name for name, deps in remaining.items()
                      if all(dependency in done for dependency in deps))
        if not wave:
            raise CycleError("the task graph has a cycle: "
                             + " -> ".join(sorted(remaining)))
        waves.append(wave)
        done.update(wave)
        for name in wave:
            remaining.pop(name)
    return waves


def run_graph(store, board, claims, registry, cfg, goal: str, *,
              tasks: Sequence[tuple], worker: Callable, workers: int = DEFAULT_WORKERS,
              run_id: str = "", store_factory: Optional[Callable] = None) -> Run:
    """Run a dependency graph: every task waits for what it declares.

    **A blocked task fails explicitly.** If `A` fails, every task that depends on
    `A` is marked failed with `blocked by A` rather than run on missing input --
    the partial-result policy the staged version left undefined.
    """
    waves = levels(tasks)
    after = {name: list(dependencies or []) for name, dependencies in tasks}
    run_id = run_id or f"run-{int(time.perf_counter() * 1000)}-{uuid.uuid4().hex[:6]}"

    def skip_if(name: str, partial: Run):
        states = {task.name: task for task in partial.tasks}
        blocked = [dependency for dependency in after.get(name, [])
                   if states.get(dependency) is not None
                   and states[dependency].state != "done"]
        return f"blocked by {', '.join(sorted(blocked))}" if blocked else None

    run = run_stages(store, board, claims, registry, cfg, goal, stages=waves,
                     worker=worker, workers=workers, run_id=run_id,
                     store_factory=store_factory, skip_if=skip_if)
    # `Run.done` and `Run.failed` are computed from the task states, so blocking a
    # task is enough -- assigning to them raised `AttributeError: property has no
    # setter`, which is the class doing its job.
    return run
