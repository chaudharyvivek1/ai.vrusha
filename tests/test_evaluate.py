"""The metrics, checked against arithmetic done by hand.

The acceptance test for Part 1 of `docs/PLAN-V3.md` is that a hand-labelled
fixture gives the same numbers as a hand calculation. So every metric here has a
worked example written out in the test, with the arithmetic in the comment.

If these numbers drift, every benchmark in `docs/BENCHMARKS.md` drifts with them.
"""
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha import evaluate
from vrusha.evaluate import Item, Outcome


def _item(document="docs/ann.md", **kwargs):
    return Item(question="q", document=document, **kwargs)


# ---------------------------------------------------------------------------
# the worked example
# ---------------------------------------------------------------------------

# Ranked documents, best first. The right one is "docs/ann.md", which appears
# third. So: recall@1 = 0, recall@5 = 1, MRR = 1/3, precision@5 = 1/5.
# nDCG@10 = (1/log2(3+1)) / 1 = 1/2 = 0.5.
RANKED = ["docs/rules.md", "docs/model.md", "docs/ann.md", "docs/serve.md",
          "docs/tools.md"]


def test_the_worked_example_by_hand():
    item = _item()
    assert evaluate.recall_at_k(RANKED, item, 1) == 0.0
    assert evaluate.recall_at_k(RANKED, item, 3) == 1.0
    assert evaluate.recall_at_k(RANKED, item, 5) == 1.0
    assert evaluate.precision_at_k(RANKED, item, 5) == pytest.approx(0.2)
    assert evaluate.reciprocal_rank(RANKED, item) == pytest.approx(1 / 3)
    assert evaluate.ndcg_at_k(RANKED, item, 10) == pytest.approx(0.5)


def test_a_hit_at_rank_one_is_a_perfect_ndcg():
    item = _item()
    assert evaluate.ndcg_at_k(["docs/ann.md", "x", "y"], item, 10) == 1.0


def test_a_hit_at_rank_five_is_one_over_log2_six():
    item = _item()
    ranked = ["a", "b", "c", "d", "docs/ann.md"]
    assert evaluate.ndcg_at_k(ranked, item, 10) == pytest.approx(1 / math.log2(6))


def test_a_miss_scores_zero_everywhere():
    item = _item()
    ranked = ["a", "b", "c"]
    assert evaluate.recall_at_k(ranked, item, 10) == 0.0
    assert evaluate.reciprocal_rank(ranked, item) == 0.0
    assert evaluate.ndcg_at_k(ranked, item, 10) == 0.0
    assert evaluate.precision_at_k(ranked, item, 3) == 0.0


def test_k_beyond_the_list_is_not_an_error():
    item = _item()
    assert evaluate.recall_at_k(RANKED, item, 100) == 1.0
    assert evaluate.precision_at_k(RANKED, item, 100) == pytest.approx(1 / 100)


def test_a_question_with_no_label_scores_zero():
    """An unlabelled question is not a pass."""
    item = Item(question="q", document="")
    assert evaluate.recall_at_k(["a"], item, 5) == 0.0
    assert evaluate.ndcg_at_k(["a"], item, 5) == 0.0


# ---------------------------------------------------------------------------
# matching is on the document, not the string
# ---------------------------------------------------------------------------


def test_a_url_matches_its_tail():
    item = Item(question="q", document="docs/ann.md")
    assert item.relevant("file:///repo/docs/ann.md", "ann")
    assert item.relevant("https://x.test/docs/ANN.md", "")   # case-insensitive
    assert not item.relevant("https://x.test/docs/rules.md", "")


# ---------------------------------------------------------------------------
# citation accuracy and faithfulness
# ---------------------------------------------------------------------------


def test_citing_the_right_document_is_one():
    outcome = Outcome(item=_item(), ranked=RANKED, cited=["docs/ann.md"])
    assert evaluate.citation_accuracy(outcome) == 1.0


def test_citing_the_wrong_document_is_zero():
    outcome = Outcome(item=_item(), ranked=RANKED, cited=["docs/rules.md"])
    assert evaluate.citation_accuracy(outcome) == 0.0


def test_citing_nothing_is_not_a_zero():
    """"Cited nothing" and "cited the wrong thing" are different failures."""
    outcome = Outcome(item=_item(), ranked=RANKED, cited=[])
    assert evaluate.citation_accuracy(outcome) is None


def test_faithfulness_counts_only_content_words():
    answer = "The index uses IVF routing and int8 quantisation."
    context = "This index uses IVF routing over int8 vectors."
    score = evaluate.faithfulness(answer, context)
    # content words in the answer: index, uses, ivf, routing, int8, quantisation
    # all but "quantisation" appear in the context: 5/6
    assert score == pytest.approx(5 / 6)


def test_an_answer_that_invents_a_term_scores_below_one():
    answer = "The index uses HNSW graphs and quantisation."
    context = "This index uses IVF routing over int8 vectors."
    assert evaluate.faithfulness(answer, context) < 1.0


def test_an_answer_with_no_content_words_has_no_score():
    assert evaluate.faithfulness("it is the a", "anything") is None


def test_an_answer_against_empty_context_scores_zero():
    assert evaluate.faithfulness("the index uses routing", "") == 0.0


def test_faithfulness_is_capped_at_one():
    answer = "routing"
    assert evaluate.faithfulness(answer, "routing routing routing") == 1.0


# ---------------------------------------------------------------------------
# passage hits
# ---------------------------------------------------------------------------


def test_a_passage_hit_needs_every_keyword():
    item = Item(question="q", document="d", keywords=("int8", "rescore"))
    assert evaluate.passage_hit(item, ["we use int8 and rescore the top rows"])
    assert not evaluate.passage_hit(item, ["we use int8"])


def test_no_keywords_means_no_passage_claim():
    item = Item(question="q", document="d")
    assert evaluate.passage_hit(item, ["anything at all"]) is False


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def _outcome(ranked, cited=(), item=None, passage=False, seconds=0.1):
    return Outcome(item=item or _item(), ranked=list(ranked), cited=list(cited),
                   passage_hit=passage, seconds=seconds)


def test_the_summary_averages_over_questions():
    outcomes = [
        _outcome(["docs/ann.md"], cited=["docs/ann.md"], passage=True),
        _outcome(["a", "b", "docs/ann.md"], cited=[], passage=False),
    ]
    summary = evaluate.summarise(outcomes)
    assert summary["questions"] == 2
    assert summary["recall@1"] == pytest.approx(0.5)
    assert summary["mrr"] == pytest.approx((1.0 + 1 / 3) / 2)
    assert summary["passage@5"] == pytest.approx(0.5)
    # one of the two cited anything, and it cited correctly
    assert summary["cited_questions"] == 1
    assert summary["citation_accuracy"] == pytest.approx(1.0)


def test_the_summary_of_nothing_is_empty():
    assert evaluate.summarise([]) == {}


def test_the_rendered_table_defines_every_metric():
    text = evaluate.render(evaluate.summarise([_outcome(["docs/ann.md"])]))
    for name in ("recall@1", "recall@5", "ndcg@10", "mrr", "citation_accuracy"):
        assert name in text
    assert "the right document is the top hit" in text


def test_rendering_nothing_says_so():
    assert evaluate.render({}) == "no questions evaluated"


def test_an_uncited_run_shows_not_applicable_not_zero():
    """0% would read as "every citation was wrong", which is a worse finding
    than "there were no citations"."""
    outcome = Outcome(item=_item(), ranked=["docs/ann.md"], cited=[])
    text = evaluate.render(evaluate.summarise([outcome]))
    assert "n/a" in text
    assert "no answer cited" in text


def test_the_built_in_set_is_twelve_labelled_questions():
    assert len(evaluate.DOCS_SET) == 12
    assert all(item.question and item.document for item in evaluate.DOCS_SET)
    assert all(item.keywords for item in evaluate.DOCS_SET)


# ---------------------------------------------------------------------------
# the empty store, and the PDF fix from the recheck
# ---------------------------------------------------------------------------


def test_an_empty_store_is_refused_not_scored(tmp_path):
    """0% recall on an empty store reads as "retrieval is broken"."""
    import os as _os
    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.store.db import Store
    from vrusha.store.embed import HashEmbedder

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    store = Store(resolve(cfg, "db"))
    with pytest.raises(RuntimeError) as error:
        evaluate.run(store, HashEmbedder(32), cfg)
    assert "no chunks" in str(error.value)
    assert "ingest" in str(error.value)
    store.close()


# ---------------------------------------------------------------------------
# the set on disk, and the loader that refuses a bad one
# ---------------------------------------------------------------------------


def test_the_docs_set_loads_forty_labelled_questions():
    items = evaluate.load_set("docs")
    assert len(items) == 40
    assert all(item.question and item.document and item.keywords for item in items)
    assert len({item.question for item in items}) == 40, "no duplicates"


def test_every_keyword_appears_in_its_own_document():
    """The check that keeps the set honest.

    A keyword that does not appear in the target document makes `passage@k`
    unachievable, and the metric silently becomes a measure of the labels.
    """
    import pathlib
    root = pathlib.Path(ROOT) / "docs"
    for item in evaluate.load_set("docs"):
        path = root / item.document
        assert path.exists(), f"{item.document} does not exist"
        text = path.read_text(encoding="utf-8").lower()
        missing = [word for word in item.keywords if word.lower() not in text]
        assert not missing, f"{item.document} has no {missing}"


def _write_set(tmp_path, payload, name="set.json"):
    import json
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_a_question_with_no_document_is_refused(tmp_path):
    path = _write_set(tmp_path, {"items": [{"question": "q", "keywords": ["a"]}]})
    with pytest.raises(evaluate.MalformedSet) as error:
        evaluate.load_set(path)
    assert "no document" in str(error.value)


def test_a_question_with_no_keywords_is_refused(tmp_path):
    path = _write_set(tmp_path, {"items": [{"question": "q", "document": "d"}]})
    with pytest.raises(evaluate.MalformedSet) as error:
        evaluate.load_set(path)
    assert "no keywords" in str(error.value)


def test_a_blank_keyword_is_refused(tmp_path):
    path = _write_set(tmp_path, {"items": [
        {"question": "q", "document": "d", "keywords": ["a", "  "]}]})
    with pytest.raises(evaluate.MalformedSet) as error:
        evaluate.load_set(path)
    assert "blank keyword" in str(error.value)


def test_a_duplicate_question_is_refused(tmp_path):
    path = _write_set(tmp_path, {"items": [
        {"question": "q", "document": "d", "keywords": ["a"]},
        {"question": "q", "document": "d", "keywords": ["a"]}]})
    with pytest.raises(evaluate.MalformedSet) as error:
        evaluate.load_set(path)
    assert "repeats" in str(error.value)


def test_a_missing_set_names_the_ones_that_exist(tmp_path):
    with pytest.raises(evaluate.MalformedSet) as error:
        evaluate.load_set(str(tmp_path / "nope.json"))
    assert "docs" in str(error.value)


def test_invalid_json_is_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(evaluate.MalformedSet) as error:
        evaluate.load_set(str(path))
    assert "not valid JSON" in str(error.value)


def test_an_empty_set_is_refused(tmp_path):
    path = _write_set(tmp_path, {"items": []})
    with pytest.raises(evaluate.MalformedSet) as error:
        evaluate.load_set(path)
    assert "non-empty" in str(error.value)


def test_a_bare_list_is_accepted(tmp_path):
    """A set written as a plain list is still a set."""
    path = _write_set(tmp_path, [{"question": "q", "document": "d",
                                  "keywords": ["a"]}])
    assert len(evaluate.load_set(path)) == 1
