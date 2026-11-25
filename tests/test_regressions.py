"""Regression tests for the Phase 1 audit findings.

One test per confirmed defect, each of which fails against commit 23958be.
Audit ids are in the test names so the report and the suite stay linked.
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS
from vrusha.harvest.gates import evaluate, gate_quality
from vrusha.languages import load_registry
from vrusha.rules import load_rules
from vrusha.store.address import MAX_SEGMENT, Address, AddressError
from vrusha.store.db import Store
from vrusha.tokens.dataset import CorpusItem, _is_validation, build_dataset, corpus_from_store


# ---------------------------------------------------------------------------
# A1-A4: segments at and beyond 100
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("2.3.1.4.99", (2, 3, 1, 4, 99)),
    ("2.3.1.4.100", (2, 3, 1, 4, 100)),
    ("2.3.1.4.999", (2, 3, 1, 4, 999)),
    ("2.3.1.100.5", (2, 3, 1, 100, 5)),
    ("2.100", (2, 100, 0, 0, 0)),
    ("2.3", (2, 3, 0, 0, 0)),
    ("2", (2, 0, 0, 0, 0)),
])
def test_a1_dotted_segments_of_any_size(text, expected):
    assert Address.parse(text).segments == expected


@pytest.mark.parametrize("ordinal", [1, 9, 10, 99, 100, 101, 500, 999])
def test_a3_float_form_round_trips_at_every_size(ordinal):
    address = Address.of(2, 3, 1, 4, ordinal)
    assert Address.parse(address.float_str()) == address


@pytest.mark.parametrize("segments", [
    (2, 3, 1, 4, 100),
    (2, 3, 100, 4, 1),
    (2, 100, 1, 4, 1),
    (5, 999, 999, 999, 999),
])
def test_a3_float_form_round_trips_with_large_segments(segments):
    address = Address.of(*segments)
    assert Address.parse(address.float_str()) == address


def test_a3_float_form_is_unchanged_for_small_addresses():
    """The widening must not move any address that was already representable."""
    assert Address.of(2, 3, 1, 4, 17).float_str() == "2.03010417"
    assert Address.of(0, 5).float_str() == "0.05000000"


def test_a4_malformed_packed_is_rejected_not_reinterpreted():
    for bad in ("2.0301041", "2.030104100", "2.03010410000", "2.0301041000000"):
        with pytest.raises(AddressError):
            Address.parse(bad)


def test_a4_packed_forms_still_parse():
    assert Address.parse("2.03010417").segments == (2, 3, 1, 4, 17)
    assert Address.parse("2.003001004100").segments == (2, 3, 1, 4, 100)
    assert Address.parse("0.05000000").segments == (0, 5, 0, 0, 0)


def test_max_segment_is_enforced_at_construction():
    with pytest.raises(AddressError):
        Address.of(2, 3, 1, 4, MAX_SEGMENT + 1)
    assert Address.of(2, 3, 1, 4, MAX_SEGMENT).segments[4] == MAX_SEGMENT


def test_addresses_survive_a_store_round_trip():
    """The parser and the renderer must agree on what the database holds."""
    for segments in [(1, 1, 3, 1, 100), (2, 1, 3, 7, 999), (1, 2, 3, 4, 5)]:
        address = Address.of(*segments)
        assert Address.parse(str(address)) == address


# ---------------------------------------------------------------------------
# C1-C2: the quality gate must not depend on the encoding
# ---------------------------------------------------------------------------

HINDI = ("भारतीय रिज़र्व बैंक ने आज कहा कि रेपो दर में कोई बदलाव नहीं किया गया है और "
         "महंगाई दर लगातार तीसरे महीने कम हो रही है। यह निर्णय मौद्रिक नीति समिति की "
         "बैठक में लिया गया। ") * 4
ENGLISH = "The Reserve Bank of India said the repo rate is unchanged and inflation is falling. " * 10
GUJARATI = "ભારતીય રિઝર્વ બેંકે જણાવ્યું કે રેપો દરમાં કોઈ ફેરફાર કરવામાં આવ્યો નથી. " * 8
TAMIL = "இந்திய ரிசர்வ் வங்கி வட்டி விகிதத்தில் மாற்றம் இல்லை என்று கூறியது. " * 8


@pytest.mark.parametrize("text", [ENGLISH, HINDI, GUJARATI, TAMIL])
def test_c1_text_ratio_does_not_depend_on_the_script(text):
    rules = load_rules()
    result = gate_quality(text, len(text.encode("utf-8")), rules)
    assert result.passed, result.reason
    assert result.detail["text_ratio"] > 0.9


def test_c1_mixed_script_is_not_penalised():
    rules = load_rules()
    mixed = (ENGLISH[:400] + " " + HINDI[:400])
    result = gate_quality(mixed, len(mixed.encode("utf-8")), rules)
    assert result.passed, result.reason


def test_c1_navigation_is_still_rejected():
    """The gate must still do its job: a link farm is not content."""
    rules = load_rules()
    links = " ".join(f"https://example.org/page{i}" for i in range(40))
    result = gate_quality(links, len(links.encode("utf-8")) * 4, rules)
    assert not result.passed


def test_c2_hindi_document_passes_the_full_gate():
    rules, registry = load_rules(), load_registry()
    report = evaluate(text=HINDI * 2, url="https://example.org/hi", licence="MIT",
                      raw_chars=len((HINDI * 2).encode("utf-8")), rules=rules,
                      registry=registry)
    assert report.passed, report.summary()


# ---------------------------------------------------------------------------
# I1: region prefixes are matched on segments, not on text
# ---------------------------------------------------------------------------

def test_i1_region_prefix_excludes_siblings(tmp_path):
    store = Store(str(tmp_path / "v.db"))
    doc = store.add_document("https://e.org/a", "a", "text", licence="MIT")
    for address in ("2.1.3.1.1", "2.10.3.1.1", "2.11.3.1.1", "2.1.3.1.2", "2.1.4.1.1"):
        chunk_id = store.add_chunk(doc, f"chunk {address} " + "word " * 40, kind="function")
        store.set_address(chunk_id, Address.parse(address))
    store.commit()

    def region(prefix):
        return sorted(i.address for i in corpus_from_store(store, min_chars=10,
                                                           address_prefix=prefix))

    assert region("2.1") == ["2.1.3.1.1", "2.1.3.1.2", "2.1.4.1.1"]
    assert region("2.1.3") == ["2.1.3.1.1", "2.1.3.1.2"]
    assert region("2") == ["2.1.3.1.1", "2.1.3.1.2", "2.1.4.1.1",
                           "2.10.3.1.1", "2.11.3.1.1"]
    store.close()


# ---------------------------------------------------------------------------
# E1-E2: the split is made per document
# ---------------------------------------------------------------------------

def test_e2_corpus_item_carries_a_document_id():
    assert "doc_id" in CorpusItem.__dataclass_fields__


def test_e1_split_key_prefers_the_document():
    assert CorpusItem(1, "x", doc_id=7).split_key == 7
    assert CorpusItem(1, "x").split_key == 1


def test_e1_no_document_lands_in_both_splits(tmp_path):
    from vrusha.tokens.bpe import train_tokenizer

    tokenizer = train_tokenizer(["alpha beta gamma delta " * 200], vocab_size=320,
                                backend="pure")
    # 60 documents, 5 chunks each: the shape that leaked before the fix
    items = []
    chunk_id = 0
    for doc_id in range(1, 61):
        for _ in range(5):
            chunk_id += 1
            items.append(CorpusItem(chunk_id, f"document {doc_id} chunk {chunk_id} "
                                             + "alpha beta gamma " * 20,
                                    "en", "paragraph", "", doc_id))
    manifest = build_dataset(items, tokenizer, str(tmp_path / "ds"),
                             shard_tokens=100_000, val_fraction=0.2)
    assert manifest["leaked_documents"] == 0
    assert manifest["document_ids"]["val"] > 0, "the split produced no validation document"


def test_e1_split_is_stable_when_new_documents_arrive():
    """Adding documents must not move an existing document across the split."""
    before = {doc: _is_validation(doc, 0.1) for doc in range(1, 101)}
    after = {doc: _is_validation(doc, 0.1) for doc in range(1, 151)}
    assert all(before[d] == after[d] for d in before)


# ---------------------------------------------------------------------------
# guard: the audit's non-defects stay non-defects
# ---------------------------------------------------------------------------

def test_a5_odd_length_packed_tail_is_still_rejected():
    with pytest.raises(AddressError):
        Address.parse("2.0301041")


def test_parse_rejects_negative_and_non_numeric():
    for bad in ("", "abc", "2.x.1", "-1.2", "2..3..4..5..6..7"):
        with pytest.raises(AddressError):
            Address.parse(bad)
