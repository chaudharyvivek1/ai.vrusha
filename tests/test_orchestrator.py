"""The loop: plan, claim, work, report — and every task ends in a state."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha import orchestrator as orch
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store.blackboard import Blackboard
from vrusha.store.claims import Claims
from vrusha.store.db import Store
from vrusha.store.registry import Registry


def _pieces(tmp_path):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    return store, board, Claims(board), Registry(board), cfg


# ---------------------------------------------------------------------------
# decomposition
# ---------------------------------------------------------------------------


def test_a_goal_splits_on_then():
    assert orch.decompose("fetch sources then extract tables") == \
        ["fetch sources", "extract tables"]


def test_a_goal_without_a_separator_is_one_task():
    assert orch.decompose("write the report") == ["write the report"]


def test_an_empty_goal_has_no_tasks():
    assert orch.decompose("") == []


def test_decomposition_is_capped():
    goal = " then ".join(f"step {index}" for index in range(20))
    assert len(orch.decompose(goal, limit=5)) == 5


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------


def test_a_run_completes_every_task(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run(store, board, claims, registry, cfg, "a then b then c",
                      worker=lambda task: f"did {task}", run_id="r1")
    assert result.done == 3 and result.failed == 0
    assert all(task.state == "done" for task in result.tasks)
    store.close()


def test_findings_and_a_report_reach_the_board(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    orch.run(store, board, claims, registry, cfg, "a then b",
             worker=lambda task: f"result for {task}", run_id="r1")
    summaries = [entry.summary for entry in board.since("r1", 0, limit=100)]
    assert any("result for a" in text for text in summaries)
    assert any("2 done" in text for text in summaries), "no report entry"
    store.close()


def test_every_task_is_marked_done_in_the_claims(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    orch.run(store, board, claims, registry, cfg, "a then b",
             worker=lambda task: "ok", run_id="r1")
    rows = claims.list("r1")
    assert {row["state"] for row in rows} == {"done"}
    store.close()


def test_slots_are_released_after_the_run(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    orch.run(store, board, claims, registry, cfg, "a then b",
             worker=lambda task: "ok", run_id="r1")
    summary = registry.summary("r1")
    assert summary["live"] == 0, f"{summary['live']} agent(s) left registered"
    store.close()


def test_a_failing_task_is_retried_once_then_failed(tmp_path):
    calls = {"n": 0}

    def flaky(task):
        calls["n"] += 1
        raise RuntimeError("the tool is down")

    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run(store, board, claims, registry, cfg, "a",
                      worker=flaky, run_id="r1")
    assert result.failed == 1
    assert result.tasks[0].attempts == orch.MAX_ATTEMPTS == 2
    assert calls["n"] == 2
    store.close()


def test_a_task_that_fails_then_succeeds_is_done(tmp_path):
    calls = {"n": 0}

    def once(task):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("flaky")
        return "recovered"

    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run(store, board, claims, registry, cfg, "a",
                      worker=once, run_id="r1")
    assert result.done == 1 and result.tasks[0].attempts == 2
    store.close()


def test_an_explicit_task_list_beats_decomposition(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run(store, board, claims, registry, cfg, "ignored goal",
                      worker=lambda task: "ok", tasks=["x", "y"], run_id="r1")
    assert [task.name for task in result.tasks] == ["x", "y"]
    store.close()


def test_the_task_cap_is_enforced(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run(store, board, claims, registry, cfg, "goal",
                      worker=lambda task: "ok",
                      tasks=[f"t{index}" for index in range(100)],
                      max_tasks=4, run_id="r1")
    assert len(result.tasks) == 4
    store.close()


def test_an_empty_goal_runs_nothing(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run(store, board, claims, registry, cfg, "",
                      worker=lambda task: "ok", run_id="r1")
    assert result.tasks == [] and result.done == 0
    store.close()


def test_render_shows_every_task(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run(store, board, claims, registry, cfg, "a then b",
                      worker=lambda task: "ok", run_id="r1")
    text = orch.render(result)
    assert "a" in text and "b" in text and "2 done" in text
    store.close()


# ---------------------------------------------------------------------------
# Part 32: the bounded pool
# ---------------------------------------------------------------------------


def test_the_pool_completes_every_task(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run_pool(store, board, claims, registry, cfg, "a then b then c",
                           worker=lambda task: f"did {task}", workers=3,
                           run_id="r1")
    assert result.done == 3 and result.failed == 0
    store.close()


def test_the_pool_never_exceeds_its_size(tmp_path):
    """The bound is the point: 64 logical agents over a small pool."""
    import threading
    import time as _time

    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def watcher(task):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        _time.sleep(0.02)
        with lock:
            state["now"] -= 1
        return "ok"

    store, board, claims, registry, cfg = _pieces(tmp_path)
    orch.run_pool(store, board, claims, registry, cfg, "g", worker=watcher,
                  tasks=[f"t{index}" for index in range(8)], workers=2,
                  run_id="r1")
    assert state["peak"] <= 2, f"{state['peak']} workers ran at once, cap was 2"
    store.close()


def test_a_failure_in_the_pool_does_not_stop_the_others(tmp_path):
    def half(task):
        if task == "bad":
            raise RuntimeError("boom")
        return "ok"

    store, board, claims, registry, cfg = _pieces(tmp_path)
    result = orch.run_pool(store, board, claims, registry, cfg, "g", worker=half,
                           tasks=["a", "bad", "c"], workers=3, run_id="r1")
    assert result.done == 2 and result.failed == 1
    store.close()


def test_the_pool_writes_one_report(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    orch.run_pool(store, board, claims, registry, cfg, "a then b",
                  worker=lambda task: "ok", workers=2, run_id="r1")
    reports = [entry for entry in board.since("r1", 0, limit=100)
               if entry.kind == "artifact"]
    assert len(reports) == 1
    store.close()


def test_the_pool_releases_every_slot(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    orch.run_pool(store, board, claims, registry, cfg, "a then b then c",
                  worker=lambda task: "ok", workers=3, run_id="r1")
    assert registry.summary("r1")["live"] == 0
    store.close()


# ---------------------------------------------------------------------------
# Part 34: the loop over HTTP
# ---------------------------------------------------------------------------


@pytest.fixture()
def remote_base(tmp_path):
    from vrusha.serve import serve_in_thread
    from vrusha.store.embed import HashEmbedder

    store, _board, _claims, _registry, cfg = _pieces(tmp_path)
    store.close()
    httpd, _thread = serve_in_thread(cfg, HashEmbedder(32), port=0)
    yield f"http://127.0.0.1:{httpd.server_address[1]}", cfg
    httpd.shutdown()
    httpd.server_close()


def test_the_remote_loop_completes_every_task(remote_base):
    base, cfg = remote_base
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.registry import Registry
    from vrusha.config import resolve

    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    result = orch.run_remote(store, board, Claims(board), Registry(board), cfg,
                             "a then b then c", worker=lambda task: f"did {task}",
                             base_url=base, workers=3, run_id="r1")
    assert result.done == 3 and result.failed == 0
    store.close()


def test_the_remote_loop_writes_findings_over_http(remote_base):
    base, cfg = remote_base
    from vrusha.config import resolve
    from vrusha.remote import RemoteBoard
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.registry import Registry

    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    orch.run_remote(store, board, Claims(board), Registry(board), cfg, "a then b",
                    worker=lambda task: f"result for {task}", base_url=base,
                    run_id="r1")
    entries = RemoteBoard(base).since("r1", 0)
    summaries = [entry["summary"] for entry in entries]
    assert any("result for a" in text for text in summaries)
    assert any("2 done" in text for text in summaries), "no report entry"
    store.close()


def test_the_remote_loop_records_claims_on_the_server(remote_base):
    base, cfg = remote_base
    from vrusha.config import resolve
    from vrusha.remote import RemoteBoard
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.registry import Registry

    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    orch.run_remote(store, board, Claims(board), Registry(board), cfg, "a",
                    worker=lambda task: "ok", base_url=base, run_id="r1")
    second = RemoteBoard(base).claim("r1", "a", "someone-else")
    assert second["granted"] is False, "the task was left claimable"
    store.close()


def test_a_remote_failure_is_retried_then_marked(remote_base):
    base, cfg = remote_base
    from vrusha.config import resolve
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.registry import Registry

    calls = {"n": 0}

    def flaky(task):
        calls["n"] += 1
        raise RuntimeError("down")

    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    result = orch.run_remote(store, board, Claims(board), Registry(board), cfg, "a",
                             worker=flaky, base_url=base, run_id="r1")
    assert result.failed == 1
    assert calls["n"] == orch.MAX_ATTEMPTS
    store.close()


def test_the_remote_loop_never_exceeds_its_pool(remote_base):
    base, cfg = remote_base
    import threading as _threading
    import time as _time

    from vrusha.config import resolve
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.claims import Claims
    from vrusha.store.db import Store
    from vrusha.store.registry import Registry

    lock = _threading.Lock()
    state = {"now": 0, "peak": 0}

    def watcher(task):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        _time.sleep(0.02)
        with lock:
            state["now"] -= 1
        return "ok"

    store = Store(resolve(cfg, "db"))
    board = Blackboard(store)
    orch.run_remote(store, board, Claims(board), Registry(board), cfg, "g",
                    worker=watcher, base_url=base, workers=2,
                    tasks=[f"t{index}" for index in range(6)], run_id="r1")
    assert state["peak"] <= 2
    store.close()


def test_an_unreachable_server_fails_the_run_without_hanging(tmp_path):
    store, board, claims, registry, cfg = _pieces(tmp_path)
    with pytest.raises(Exception):
        orch.run_remote(store, board, claims, registry, cfg, "a",
                        worker=lambda task: "ok",
                        base_url="http://127.0.0.1:9", run_id="r1")
    store.close()
