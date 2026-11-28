"""Compaction: keep what matters, prove it, and decline when it does not help."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import compaction
from vrusha.store.blackboard import Entry


def entries(kinds, size=40):
    made = []
    for index, kind in enumerate(kinds, start=1):
        text = f"{kind} number {index} " + ("detail " * size)
        entry = Entry(run="r", seq=index, task=f"t{index % 3}", kind=kind,
                      summary=text)
        if kind == "artifact":
            entry.refs = [f"file:{index}.csv"]
        made.append(entry)
    return made


def test_the_threshold_is_85_percent():
    assert compaction.THRESHOLD == 0.85


def test_compaction_triggers_near_the_budget():
    small = entries(["progress"] * 5, size=1)
    assert compaction.should_compact(small, budget_tokens=10000) is False
    big = entries(["progress"] * 200, size=40)
    assert compaction.should_compact(big, budget_tokens=1000) is True


def test_a_summary_records_the_seq_range_it_replaced():
    made = entries(["finding", "progress", "decision", "progress", "artifact",
                    "error"] + ["progress"] * 30, size=40)
    summary = compaction.compact(made)
    assert (summary.seq_from, summary.seq_to) == (1, 36)
    assert summary.entries_replaced == 36
    assert "seq 1-36" in summary.text


def test_decisions_errors_and_questions_survive():
    # padded with droppable progress: compaction only pays on a long run, which
    # is the case it exists for
    made = entries(["decision", "error", "question"] + ["progress"] * 30, size=40)
    summary = compaction.compact(made)
    assert "decision number 1" in summary.text
    assert "error number 2" in summary.text
    assert "question number 3" in summary.text


def test_artifact_references_survive():
    made = entries(["artifact"] + ["progress"] * 30, size=40)
    summary = compaction.compact(made)
    assert "file:1.csv" in summary.text


def test_only_the_last_finding_per_task_is_kept():
    made = [Entry(run="r", seq=1, task="t1", kind="finding", summary="first " + "x" * 200),
            Entry(run="r", seq=2, task="t1", kind="finding", summary="second " + "x" * 200)]
    made += [Entry(run="r", seq=index, task="t2", kind="progress",
                   summary="step " + "y" * 200) for index in range(3, 33)]
    summary = compaction.compact(made)
    assert "second" in summary.text
    assert "first" not in summary.text


def test_progress_is_dropped():
    made = entries(["progress"] * 20, size=40)
    summary = compaction.compact(made)
    assert summary.dropped == 20


def test_a_real_compaction_shrinks_the_text():
    made = entries(["progress"] * 40 + ["decision"], size=60)
    summary = compaction.compact(made)
    assert summary.useful is True
    assert summary.tokens_after < summary.tokens_before
    assert summary.ratio < 0.5


def test_compaction_declines_when_it_would_grow_the_text():
    """The first version produced a summary 2.7x LARGER than six tiny entries."""
    made = entries(["finding", "progress", "decision"], size=0)
    summary = compaction.compact(made)
    assert summary.useful is False
    assert summary.text == ""
    assert summary.tokens_after == summary.tokens_before


def test_verify_passes_on_a_good_summary():
    made = entries(["decision", "error", "artifact"] + ["progress"] * 20, size=40)
    summary = compaction.compact(made)
    assert compaction.verify(summary, made)["ok"] is True


def test_verify_catches_a_dropped_decision():
    made = entries(["decision"] + ["progress"] * 30, size=40)
    summary = compaction.compact(made)
    summary.text = summary.text.replace("decision number 1", "nothing")
    result = compaction.verify(summary, made)
    assert result["ok"] is False
    assert any("decision" in problem for problem in result["problems"])


def test_verify_catches_a_dropped_artifact():
    made = entries(["artifact"] + ["progress"] * 15, size=40)
    summary = compaction.compact(made)
    summary.text = summary.text.replace("file:1.csv", "")
    assert compaction.verify(summary, made)["ok"] is False


def test_empty_input_is_not_an_error():
    summary = compaction.compact([])
    assert summary.entries_replaced == 0 and summary.text == ""


def test_a_huge_entry_needs_offloading():
    big = Entry(run="r", seq=1, kind="finding", summary="x" * (4 * 20001))
    assert compaction.offload_needed(big) is True
    small = Entry(run="r", seq=1, kind="finding", summary="x" * 100)
    assert compaction.offload_needed(small) is False
