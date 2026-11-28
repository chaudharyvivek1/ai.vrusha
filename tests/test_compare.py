"""Four levels against one agent: the same answers, and what the hierarchy costs."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha import compare
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.harvest.fetch import Document
from vrusha.ingest import ingest_documents
from vrusha.languages import load_registry
from vrusha.rules import load_rules
from vrusha.store.blackboard import Blackboard
from vrusha.store.claims import Claims
from vrusha.store.db import Store
from vrusha.store.embed import HashEmbedder
from vrusha.store.registry import Registry


def _setup(tmp_path):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    ingest_documents(
        [Document(url="https://a.test/1", title="ann", status=200,
                  text="the vector index uses ivf routing and int8. " * 20),
         Document(url="https://a.test/2", title="rules", status=200,
                  text="a licence gate refuses a page with no licence. " * 20)],
        store, cfg, load_rules(), load_registry(), HashEmbedder(64),
        licence="MIT", verbose=False)
    board = Blackboard(store)
    return store, cfg, board, Claims(board), Registry(board), HashEmbedder(64)


def _items():
    from vrusha.evaluate import Item
    return [Item(question="how does the index route?", document="ann.md",
                 keywords=["ivf"]),
            Item(question="what licence is needed?", document="rules.md",
                 keywords=["licence"])]


def test_both_runs_answer_the_same_questions(tmp_path):
    store, cfg, board, claims, registry, embedder = _setup(tmp_path)
    result = compare.run(store, embedder, cfg, board, claims, registry,
                         _items(), run_id="c1")
    assert result.questions == 2
    assert result.tasks == 2
    store.close()


def test_quality_cannot_differ_because_the_retrieval_is_the_same(tmp_path):
    """The honest finding: identical answers, and the test says why."""
    store, cfg, board, claims, registry, embedder = _setup(tmp_path)
    result = compare.run(store, embedder, cfg, board, claims, registry,
                         _items(), run_id="c1")
    assert result.quality_delta == pytest.approx(0.0)
    assert result.single["recall@5"] == result.orchestrated["recall@5"]
    store.close()


def test_the_hierarchy_leaves_a_record(tmp_path):
    store, cfg, board, claims, registry, embedder = _setup(tmp_path)
    result = compare.run(store, embedder, cfg, board, claims, registry,
                         _items(), run_id="c1")
    assert result.board_entries > 0, "the run left no trace"
    store.close()


def test_the_cost_ratio_is_reported(tmp_path):
    store, cfg, board, claims, registry, embedder = _setup(tmp_path)
    result = compare.run(store, embedder, cfg, board, claims, registry,
                         _items(), run_id="c1")
    assert result.cost_ratio > 0
    assert "cost ratio" in compare.render(result)
    store.close()


def test_render_shows_both_columns(tmp_path):
    store, cfg, board, claims, registry, embedder = _setup(tmp_path)
    result = compare.run(store, embedder, cfg, board, claims, registry,
                         _items(), run_id="c1")
    text = compare.render(result)
    assert "single" in text and "orchestrated" in text
    assert "recall@5" in text
    store.close()


def test_the_graph_shape_matches_the_stages_shape(tmp_path):
    """The same plan written two ways must produce the same answer.

    If they disagree, one of the two shapes is wrong -- which is the whole reason
    to write the general one.
    """
    store, cfg, board, claims, registry, embedder = _setup(tmp_path)
    import uuid as _uuid

    staged = compare.multistep(store, embedder, cfg, board, claims, registry,
                               ["ann", "rules"], workers=2,
                               run_id=f"a{_uuid.uuid4().hex[:6]}", shape="stages")
    graphed = compare.multistep(store, embedder, cfg, board, claims, registry,
                                ["ann", "rules"], workers=2,
                                run_id=f"b{_uuid.uuid4().hex[:6]}", shape="graph")
    assert graphed.orchestrated_facts == staged.orchestrated_facts
    assert graphed.tasks == staged.tasks
    store.close()


def test_an_unknown_shape_is_refused(tmp_path):
    store, cfg, board, claims, registry, embedder = _setup(tmp_path)
    with pytest.raises(ValueError) as error:
        compare.multistep(store, embedder, cfg, board, claims, registry,
                          ["ann"], run_id="x", shape="triangle")
    assert "triangle" in str(error.value)
    store.close()


def test_a_reused_run_id_poisons_the_board(tmp_path):
    """Why every measurement uses a fresh id: claims are per run, so a second run
    with the same id finds its tasks already `done` and its board already written.
    Found by measuring the same run_id twice -- the numbers silently improved."""
    store, cfg, board, claims, registry, embedder = _setup(tmp_path)
    first = compare.multistep(store, embedder, cfg, board, claims, registry,
                              ["ann", "rules"], workers=2, run_id="same")
    second = compare.multistep(store, embedder, cfg, board, claims, registry,
                               ["ann", "rules"], workers=2, run_id="same")
    assert second.board_entries > first.board_entries, \
        "the second run should show the first run's entries as well"
    store.close()
