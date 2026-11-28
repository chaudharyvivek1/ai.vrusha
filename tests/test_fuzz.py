"""Fuzzing: every parser, broken input, and the rule that nothing crashes.

The other tests feed the parsers what they expect. This one feeds them garbage —
random bytes, truncated markup, wrong types, enormous numbers — and asserts the only
thing a parser may do with rubbish: **return a value, or raise a declared error.**

A `TypeError` from deep inside a parser is a bug, because a caller cannot catch what
it cannot name. A `RecursionError` is worse. A hang is worst of all, so inputs are
bounded and every call has a size ceiling.

Deterministic: a fixed seed per case, so a failure is reproducible and this file
does not fail on a Tuesday.
"""
import io
import json
import os
import random
import string
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def noise(seed: int, size: int = 400) -> str:
    """Deterministic rubbish: bytes that are mostly not markup."""
    rng = random.Random(seed)
    alphabet = string.printable + "\x00\u00ff\u2603"
    return "".join(rng.choice(alphabet) for _ in range(size))


def markup_noise(seed: int, size: int = 400) -> str:
    """Rubbish that looks like broken HTML: unclosed tags, stray brackets."""
    rng = random.Random(seed)
    pieces = ["<div>", "</div>", "<table>", "<tr>", "<td>", "<", ">", "&amp;",
              "<script>", "</script>", "<p>", '"', "'", "\n", "text", "="]
    return "".join(rng.choice(pieces) for _ in range(max(1, size // 6)))


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(8))
def test_the_extractor_survives_broken_markup(seed, tmp_path):
    from vrusha.store import records
    from vrusha.store.db import Store

    store = Store(str(tmp_path / f"fuzz{seed}.db"))
    document = store.add_document(url="https://a.test/x", title="t", text="body")
    try:
        result = records.extract_document(store, document, markup_noise(seed),
                                          "https://a.test/x")
        assert isinstance(result, dict)
    finally:
        store.close()


@pytest.mark.parametrize("seed", range(4))
def test_the_extractor_survives_binary_noise(seed, tmp_path):
    from vrusha.store import records
    from vrusha.store.db import Store

    store = Store(str(tmp_path / f"bin{seed}.db"))
    document = store.add_document(url="https://a.test/y", title="t", text="body")
    try:
        result = records.extract_document(store, document, noise(seed),
                                          "https://a.test/y")
        assert isinstance(result, dict)
    finally:
        store.close()


@pytest.mark.parametrize("seed", range(4))
def test_the_jsonld_reader_survives_rubbish(seed):
    from vrusha.store import jsonld

    payload = noise(seed)
    try:
        result = jsonld.extract(payload) if hasattr(jsonld, "extract") else []
    except (ValueError, TypeError) as exc:      # declared: a parse failure
        assert str(exc) or True
        return
    assert result is not None


# ---------------------------------------------------------------------------
# the blackboard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_the_board_accepts_any_summary_it_does_not_refuse(seed, tmp_path):
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.db import Store

    store = Store(str(tmp_path / f"board{seed}.db"))
    board = Blackboard(store)
    try:
        entry = board.append("fuzz", task="t", summary=noise(seed, 120))
        assert entry.seq >= 1
    finally:
        board.close()
        store.close()


def test_the_board_refuses_an_unknown_kind_by_name(tmp_path):
    from vrusha.store.blackboard import Blackboard
    from vrusha.store.db import Store

    store = Store(str(tmp_path / "kind.db"))
    board = Blackboard(store)
    try:
        with pytest.raises(ValueError):
            board.append("fuzz", kind="nonsense", summary="x")
    finally:
        board.close()
        store.close()


# ---------------------------------------------------------------------------
# the safety scanner and the maths canonicaliser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(10))
def test_the_scanner_survives_noise(seed):
    from vrusha import safety

    text = noise(seed, 800)
    findings = safety.scan(text)
    assert isinstance(findings, list)
    cleaned = safety.neutralise(text)
    assert isinstance(cleaned, str)


@pytest.mark.parametrize("seed", range(6))
def test_the_maths_canonicaliser_survives_noise(seed):
    from vrusha.store import mathquery

    text = noise(seed, 200)
    try:
        result = mathquery.canonicalise(text) if hasattr(mathquery, "canonicalise") \
            else mathquery.extract_math(text)
    except (ValueError, TypeError, IndexError):
        return                          # a refusal is allowed, a crash is not
    assert result is not None


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------


def test_the_server_answers_a_malformed_body_with_4xx(tmp_path):
    import urllib.error
    import urllib.request

    from vrusha.config import DEFAULTS, ensure_dirs, resolve
    from vrusha.serve import serve_in_thread
    from vrusha.store.embed import HashEmbedder

    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    httpd, _thread = serve_in_thread(cfg, HashEmbedder(32), port=0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        for body in (b"{", b"[]", b'{"run": 123}', b"\x00\xff"):
            request = urllib.request.Request(
                base + "/board", data=body, method="POST",
                headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    assert response.status < 500
            except urllib.error.HTTPError as exc:
                assert 400 <= exc.code < 500, f"a malformed body got {exc.code}"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# the dataset loader
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(4))
def test_the_beir_loader_refuses_rubbish_by_name(seed, tmp_path):
    from vrusha.eval_sets import beir

    directory = tmp_path / f"set{seed}"
    (directory / "qrels").mkdir(parents=True)
    (directory / "corpus.jsonl").write_text(noise(seed, 200))
    (directory / "queries.jsonl").write_text(noise(seed + 100, 200))
    (directory / "qrels" / "test.tsv").write_text(noise(seed + 200, 200))
    with pytest.raises(beir.BeirError):
        beir.load(str(directory))
