"""Claims: exactly one agent per task, and a lease so a dead agent lets go.

The acceptance test from `docs/AGENTS.md` step 2: 64 agents racing for 10 tasks
produce exactly 10 winners. Everything else here is about the edges of that — a
live lease that must not be broken, an expired one that must be, and a finished
task that must never be re-issued.
"""
import multiprocessing
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store.blackboard import Blackboard
from vrusha.store.claims import Claims
from vrusha.store.db import Store


@pytest.fixture()
def claims(tmp_path):
    store = Store(str(tmp_path / "claims.db"), check_same_thread=False)
    board = Blackboard(store)
    yield Claims(board)
    board.close()
    store.close()


# ---------------------------------------------------------------------------
# the acceptance test
# ---------------------------------------------------------------------------


def test_sixty_four_agents_racing_for_ten_tasks_produce_ten_winners(claims):
    tasks = [f"task-{index}" for index in range(10)]
    winners = []
    lock = threading.Lock()

    def work(agent: int):
        for task in tasks:
            result = claims.claim("run", task, f"L4/{agent}")
            if result.granted:
                with lock:
                    winners.append((task, f"L4/{agent}"))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(64)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 10, f"{len(winners)} winners for 10 tasks"
    claimed = [task for task, _agent in winners]
    assert len(set(claimed)) == 10, "a task was granted twice"
    assert set(claimed) == set(tasks)


def _process_racer(path: str, agent: int, tasks, barrier, winners_path: str) -> None:
    import json
    import traceback

    try:
        store = Store(path)
        board = Blackboard(store)
        claims = Claims(board)
        try:
            barrier.wait()
            won = []
            for task in tasks:
                if claims.claim("run", task, f"proc-{agent}").granted:
                    won.append(task)
            with open(f"{winners_path}.{agent}", "w", encoding="utf-8") as handle:
                json.dump(won, handle)
        finally:
            board.close()
            store.close()
    except Exception:                    # noqa: BLE001 - a child must say why
        traceback.print_exc()
        raise


def test_claims_hold_across_processes(tmp_path):
    """The same race, through SQLite's file lock instead of a thread lock."""
    import json

    path = str(tmp_path / "claims.db")
    store = Store(path)
    setup = Blackboard(store)
    setup.close()
    store.close()

    tasks = [f"task-{index}" for index in range(10)]
    racers = 6
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(racers)
    prefix = str(tmp_path / "won")
    processes = [context.Process(target=_process_racer,
                                 args=(path, n, tasks, barrier, prefix))
                 for n in range(racers)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)
    assert all(process.exitcode == 0 for process in processes), \
        [process.exitcode for process in processes]

    won = []
    for index in range(racers):
        with open(f"{prefix}.{index}", encoding="utf-8") as handle:
            won.extend(json.load(handle))
    assert len(won) == 10, f"{len(won)} winners for 10 tasks"
    assert len(set(won)) == 10, "a task was granted to two processes"


# ---------------------------------------------------------------------------
# leases
# ---------------------------------------------------------------------------


def test_a_live_lease_is_never_broken(claims):
    first = claims.claim("run", "t", "a", ttl=60, now=1000.0)
    second = claims.claim("run", "t", "b", now=1030.0)
    assert first.granted and not second.granted
    assert "held by a" in second.reason
    assert second.attempts == 1, "a refused claim is not an attempt"


def test_an_expired_lease_is_taken_over(claims):
    claims.claim("run", "t", "a", ttl=10, now=1000.0)
    taken = claims.claim("run", "t", "b", now=1011.0)
    assert taken.granted
    assert taken.attempts == 2, "the second attempt is counted"


def test_attempts_accumulate_so_a_poison_task_is_visible(claims):
    for attempt in range(4):
        claims.claim("run", "t", f"a{attempt}", ttl=1, now=1000.0 + attempt * 10)
    assert claims.claim("run", "t", "late", now=2000.0).attempts == 5


def test_renewing_someone_elses_lease_is_refused(claims):
    claims.claim("run", "t", "a", ttl=10, now=1000.0)
    assert claims.renew("run", "t", "b", now=1001.0).granted is False
    assert claims.renew("run", "t", "a", now=1001.0).granted is True


def test_renewing_extends_the_deadline(claims):
    claims.claim("run", "t", "a", ttl=10, now=1000.0)
    claims.renew("run", "t", "a", ttl=60, now=1005.0)
    # would have expired at 1010 without the renewal
    assert claims.claim("run", "t", "b", now=1050.0).granted is False


def test_an_expired_lease_cannot_be_renewed(claims):
    claims.claim("run", "t", "a", ttl=10, now=1000.0)
    assert claims.renew("run", "t", "a", now=1020.0).granted is False


# ---------------------------------------------------------------------------
# finishing and giving back
# ---------------------------------------------------------------------------


def test_only_the_holder_can_complete(claims):
    claims.claim("run", "t", "a", ttl=60)
    assert claims.complete("run", "t", "b") is False
    assert claims.complete("run", "t", "a") is True


def test_a_finished_task_is_never_reissued(claims):
    claims.claim("run", "t", "a", ttl=60, now=1000.0)
    claims.complete("run", "t", "a")
    later = claims.claim("run", "t", "b", now=99999.0)
    assert later.granted is False
    assert "already done" in later.reason


def test_releasing_makes_the_task_claimable_at_once(claims):
    claims.claim("run", "t", "a", ttl=600)
    assert claims.release("run", "t", "a") is True
    assert claims.claim("run", "t", "b").granted is True


def test_a_failed_task_can_be_retried(claims):
    claims.claim("run", "t", "a", ttl=600)
    claims.complete("run", "t", "a", status="failed", note="site returned 503")
    retried = claims.claim("run", "t", "b")
    assert retried.granted
    assert retried.attempts == 2


def test_the_reaper_expires_only_what_ran_out(claims):
    claims.claim("run", "short", "a", ttl=10, now=1000.0)
    claims.claim("run", "long", "b", ttl=1000, now=1000.0)
    assert claims.reap("run", now=1020.0) == 1
    states = {row["task"]: row["state"] for row in claims.list("run")}
    assert states == {"short": "failed", "long": "claimed"}


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_a_claim_without_a_task_is_refused(claims):
    with pytest.raises(ValueError):
        claims.claim("run", "", "a")


def test_a_lease_with_no_duration_is_refused(claims):
    """A lease that never expires is a lock nobody releases."""
    with pytest.raises(ValueError) as error:
        claims.claim("run", "t", "a", ttl=0)
    assert "no duration" in str(error.value)


def test_an_unknown_completion_status_is_refused(claims):
    claims.claim("run", "t", "a")
    with pytest.raises(ValueError):
        claims.complete("run", "t", "a", status="maybe")


def test_summary_counts_every_state(claims):
    claims.claim("run", "a", "x")
    claims.claim("run", "b", "x")
    claims.complete("run", "a", "x")
    claims.claim("run", "c", "y", ttl=1, now=1000.0)
    claims.reap("run", now=2000.0)
    assert claims.summary("run") == {"claimed": 1, "done": 1, "failed": 1,
                                     "total": 3}
