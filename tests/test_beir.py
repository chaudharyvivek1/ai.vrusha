"""BEIR sets: someone else's labels, and a loader that refuses a broken set."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.eval_sets import beir


def _write(tmp_path, corpus, queries, qrels):
    directory = tmp_path / "set"
    (directory / "qrels").mkdir(parents=True)
    (directory / "corpus.jsonl").write_text(
        "\n".join(json.dumps(row) for row in corpus) + "\n", encoding="utf-8")
    (directory / "queries.jsonl").write_text(
        "\n".join(json.dumps(row) for row in queries) + "\n", encoding="utf-8")
    (directory / "qrels" / "test.tsv").write_text(qrels, encoding="utf-8")
    return str(directory)


CORPUS = [{"_id": "d1", "title": "a", "text": "first document"},
          {"_id": "d2", "title": "b", "text": "second document"}]
QUERIES = [{"_id": "q1", "text": "a question"}]


# ---------------------------------------------------------------------------
# the format
# ---------------------------------------------------------------------------


def test_a_well_formed_set_loads(tmp_path):
    path = _write(tmp_path, CORPUS, QUERIES,
                  "query-id\tcorpus-id\tscore\nq1\td1\t1\n")
    loaded = beir.load(path, name="mine")
    assert loaded.documents == 2 and len(loaded.queries) == 1
    assert loaded.judgements == 1
    assert loaded.name == "mine"


def test_a_missing_file_is_refused_with_the_names(tmp_path):
    with pytest.raises(beir.BeirError) as error:
        beir.load(str(tmp_path))
    assert "corpus.jsonl" in str(error.value)


def test_bad_json_names_the_line(tmp_path):
    directory = tmp_path / "bad"
    (directory / "qrels").mkdir(parents=True)
    (directory / "corpus.jsonl").write_text('{"_id": "d1"}\nnot json\n')
    (directory / "queries.jsonl").write_text('{"_id": "q1", "text": "x"}\n')
    (directory / "qrels" / "test.tsv").write_text("q1\td1\t1\n")
    with pytest.raises(beir.BeirError) as error:
        beir.load(str(directory))
    assert "corpus.jsonl:2" in str(error.value)


def test_a_short_qrels_row_names_the_line(tmp_path):
    directory = tmp_path / "short"
    (directory / "qrels").mkdir(parents=True)
    (directory / "corpus.jsonl").write_text('{"_id": "d1", "text": "x"}\n')
    (directory / "queries.jsonl").write_text('{"_id": "q1", "text": "x"}\n')
    (directory / "qrels" / "test.tsv").write_text(
        "query-id\tcorpus-id\tscore\nq1\td1\n")
    with pytest.raises(beir.BeirError) as error:
        beir.load(str(directory))
    assert "three tab-separated columns" in str(error.value)


def test_the_header_row_is_not_data(tmp_path):
    path = _write(tmp_path, CORPUS, QUERIES,
                  "query-id\tcorpus-id\tscore\nq1\td1\t1\n")
    assert "query-id" not in beir.load(path).qrels


def test_zero_and_negative_scores_are_not_relevance(tmp_path):
    path = _write(tmp_path, CORPUS, QUERIES,
                  "query-id\tcorpus-id\tscore\nq1\td1\t1\nq1\td2\t0\n")
    assert beir.load(path).qrels["q1"] == {"d1": 1}


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def test_a_perfect_ranking_scores_one():
    result = beir.score(["d1", "d2"], {"d1": 1}, 10)
    assert result["recall"] == 1.0 and result["mrr"] == 1.0
    assert result["ndcg"] == 1.0


def test_a_miss_scores_zero():
    result = beir.score(["d9"], {"d1": 1}, 10)
    assert result["recall"] == 0.0 and result["mrr"] == 0.0


def test_rank_two_is_worth_half_in_mrr():
    assert beir.score(["d9", "d1"], {"d1": 1}, 10)["mrr"] == 0.5


def test_recall_counts_every_relevant_document():
    assert beir.score(["d1"], {"d1": 1, "d2": 1}, 10)["recall"] == 0.5


def test_the_cutoff_is_respected():
    assert beir.score(["d9", "d8", "d1"], {"d1": 1}, 2)["recall"] == 0.0


# ---------------------------------------------------------------------------
# the bundled fixture
# ---------------------------------------------------------------------------


def test_the_bundled_set_loads():
    loaded = beir.bundled()
    assert loaded.documents == 5
    assert len(loaded.queries) == 5
    assert loaded.judgements == 5


def test_every_bundled_query_has_a_judgement():
    loaded = beir.bundled()
    assert {query["_id"] for query in loaded.queries} == set(loaded.qrels)


def test_every_judged_document_is_in_the_corpus():
    loaded = beir.bundled()
    ids = {row["_id"] for row in loaded.corpus}
    judged = {doc for rows in loaded.qrels.values() for doc in rows}
    assert judged <= ids
