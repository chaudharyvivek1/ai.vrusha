"""The registry: 64 slots, a heartbeat, and a cap that refuses.

The acceptance test from `docs/AGENTS.md` step 3: `GET /agents` shows 64 slots, a
killed agent's slot goes `dead` within 2× heartbeat, and no two live agents share
a slot id.

The last one is the concurrency test, and it is the one that catches the bug this
family of modules keeps producing: a read-then-write gap between "find a free
slot" and "take it".
"""
import multiprocessing
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store.blackboard import Blackboard
from vrusha.store.db import Store
from vrusha.store.registry import MAX_AGENTS, Registry


@pytest.fixture()
def registry(tmp_path):
    store = Store(str(tmp_path / "registry.db"), check_same_thread=False)
    board = Blackboard(store)
    yield Registry(board)
    board.close()
    store.close()


# ---------------------------------------------------------------------------
# the cap
# ---------------------------------------------------------------------------


def test_a_run_starts_with_sixty_four_slots(registry):
    registry.open_run("run")
    slots = registry.slots("run")
    assert len(slots) == MAX_AGENTS == 64
    assert [slot.slot for slot in slots] == list(range(1, 65))
    assert all(slot.state == "free" for slot in slots)


def test_agents_take_the_lowest_free_slot(registry):
    assert registry.register("run", "a").slot == 1
    assert registry.register("run", "b").slot == 2
    registry.release("run", "a")
    assert registry.register("run", "c").slot == 1, "the lowest free slot is reused"


def test_the_sixty_fifth_agent_is_refused(registry):
    """A cap that lives in a document is a cap that does not exist."""
    for index in range(64):
        assert registry.register("run", f"agent-{index}").registered
    overflow = registry.register("run", "agent-65")
    assert overflow.registered is False
    assert overflow.slot == 0
    assert "all 64 slots are live" in overflow.reason
    assert registry.summary("run")["live"] == 64


def test_a_refused_agent_does_not_replace_a_live_one(registry):
    for index in range(64):
        registry.register("run", f"agent-{index}")
    registry.register("run", "late")
    names = {slot.agent for slot in registry.live("run")}
    assert len(names) == 64
    assert "late" not in names


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_registering_twice_keeps_the_same_slot(registry):
    first = registry.register("run", "a", skill="extract")
    second = registry.register("run", "a", skill="fetch")
    assert first.slot == second.slot == 1
    assert second.skill == "fetch", "re-registering updates the skill"
    assert registry.summary("run")["live"] == 1, "one agent, not two"


def test_an_agent_with_no_name_is_refused(registry):
    with pytest.raises(ValueError):
        registry.register("run", "")


def test_no_two_live_agents_share_a_slot(registry):
    for index in range(64):
        registry.register("run", f"agent-{index}")
    slots = [slot.slot for slot in registry.live("run")]
    assert len(slots) == len(set(slots)) == 64


# ---------------------------------------------------------------------------
# heartbeats and death
# ---------------------------------------------------------------------------


def test_a_heartbeat_keeps_an_agent_alive(registry):
    registry.register("run", "a", now=1000.0)
    assert registry.heartbeat("run", "a", now=1050.0) is True
    assert registry.sweep("run", stale_after=90, now=1100.0) == []


def test_an_agent_that_stops_beating_goes_dead(registry):
    registry.register("run", "quiet", now=1000.0)
    registry.register("run", "busy", now=1000.0)
    # the cutoff at now=1200 with stale_after=90 is 1110, so a beat at 1080 is
    # already too old; 1150 is inside the window
    registry.heartbeat("run", "busy", now=1150.0)
    dead = registry.sweep("run", stale_after=90, now=1200.0)
    assert dead == ["quiet"]
    states = {slot.agent: slot.state for slot in registry.slots("run") if slot.agent}
    assert states == {"quiet": "dead", "busy": "live"}


def test_a_dead_slot_can_be_taken_by_a_new_agent(registry):
    registry.register("run", "gone", now=1000.0)
    registry.sweep("run", stale_after=90, now=1200.0)
    fresh = registry.register("run", "replacement", now=1201.0)
    assert fresh.registered and fresh.slot == 1


def test_a_heartbeat_does_not_revive_a_dead_agent(registry):
    registry.register("run", "gone", now=1000.0)
    registry.sweep("run", stale_after=90, now=1200.0)
    assert registry.heartbeat("run", "gone", now=1201.0) is False


def test_the_sweep_boundary_is_the_stale_after_window(registry):
    registry.register("run", "a", now=1000.0)
    assert registry.sweep("run", stale_after=90, now=1089.0) == []
    assert registry.sweep("run", stale_after=90, now=1091.0) == ["a"]


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def test_the_summary_adds_up_to_the_capacity(registry):
    registry.register("run", "a")
    registry.register("run", "b")
    registry.register("run", "c", now=1000.0)
    registry.sweep("run", stale_after=1, now=2000.0)
    summary = registry.summary("run")
    assert summary["live"] + summary["dead"] + summary["free"] == summary["total"] == 64


def test_agents_are_grouped_by_skill(registry):
    registry.register("run", "a", skill="extract-tables")
    registry.register("run", "b", skill="extract-tables")
    registry.register("run", "c", skill="fetch")
    assert registry.by_skill("run") == {"extract-tables": 2, "fetch": 1}


def test_an_empty_run_has_no_live_agents(registry):
    registry.open_run("run")
    assert registry.live("run") == []
    assert registry.summary("run")["live"] == 0


# ---------------------------------------------------------------------------
# the concurrency test
# ---------------------------------------------------------------------------


def test_sixty_four_threads_get_sixty_four_distinct_slots(registry):
    """The read-then-write gap, if it exists, shows up as a shared slot."""
    results = []
    lock = threading.Lock()

    def work(index: int):
        slot = registry.register("run", f"agent-{index}")
        with lock:
            results.append((index, slot.slot, slot.registered))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(64)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    granted = [(index, slot) for index, slot, ok in results if ok]
    assert len(granted) == 64, f"only {len(granted)} of 64 agents registered"
    slots = [slot for _index, slot in granted]
    assert len(set(slots)) == 64, "two agents were given the same slot"


def _process_agent(path: str, index: int, barrier, out_dir: str) -> None:
    import json
    import traceback

    try:
        store = Store(path)
        board = Blackboard(store)
        registry = Registry(board)
        try:
            barrier.wait()
            slot = registry.register("run", f"proc-{index}")
            with open(os.path.join(out_dir, f"slot.{index}"), "w",
                      encoding="utf-8") as handle:
                json.dump({"slot": slot.slot, "ok": slot.registered}, handle)
        finally:
            board.close()
            store.close()
    except Exception:                        # noqa: BLE001 - a child must say why
        traceback.print_exc()
        raise


def test_registration_holds_across_processes(tmp_path):
    import json

    path = str(tmp_path / "registry.db")
    store = Store(path)
    setup = Blackboard(store)
    Registry(setup).open_run("run")
    setup.close()
    store.close()

    racers = 8
    # `spawn`, not `fork` -- see the note in test_claims.py
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(racers)
    out_dir = str(tmp_path)
    processes = [context.Process(target=_process_agent,
                                 args=(path, n, barrier, out_dir))
                 for n in range(racers)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)
    assert all(process.exitcode == 0 for process in processes), \
        [process.exitcode for process in processes]

    slots = []
    for index in range(racers):
        with open(os.path.join(out_dir, f"slot.{index}"), encoding="utf-8") as handle:
            payload = json.load(handle)
        assert payload["ok"]
        slots.append(payload["slot"])
    assert len(set(slots)) == racers, "two processes were given the same slot"
