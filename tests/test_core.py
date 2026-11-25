"""Core unit tests: the address scheme, the rule resolver, the language registry.

These three are the parts everything else assumes, so they are tested without
a database, a network or a model in the way.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vrusha.languages import LanguageError, load_registry
from vrusha.rules import LEVELS, RuleError, load_rules
from vrusha.store.address import Address, AddressError, compose, spread


# --------------------------------------------------------------------------
# addresses
# --------------------------------------------------------------------------

def test_parse_accepts_every_form():
    assert Address.parse("2.3.1.4.17").segments == (2, 3, 1, 4, 17)
    assert Address.parse("2.03010417").segments == (2, 3, 1, 4, 17)
    assert Address.parse("0.5").segments == (0, 5, 0, 0, 0)
    assert Address.parse("0.05").segments == (0, 5, 0, 0, 0)
    assert Address.parse("0.05000000").segments == (0, 5, 0, 0, 0)
    assert Address.parse("2").segments == (2, 0, 0, 0, 0)
    assert Address.parse((1, 2)).segments == (1, 2, 0, 0, 0)


def test_parse_rejects_nonsense():
    for bad in ("", "abc", "2.x.1", "-1.2"):
        with pytest.raises(AddressError):
            Address.parse(bad)


def test_segments_are_padded_and_bounded():
    assert Address.of(1).segments == (1, 0, 0, 0, 0)
    with pytest.raises(AddressError):
        Address.of(1, 2, 3, 4, 5, 6)
    with pytest.raises(AddressError):
        Address.of(1, -2)


def test_prefix_is_lossless_and_str_drops_padding():
    address = Address.of(2, 3, 1, 4, 17)
    assert str(address.prefix(4)) == "2.3.1.4"
    assert Address.parse(str(address.prefix(4))) == address.prefix(4)
    assert str(address) == "2.3.1.4.17"
    assert str(Address.of(0, 5)) == "0.5"


def test_float_form_round_trips():
    address = Address.of(2, 3, 1, 4, 17)
    assert address.float_str() == "2.03010417"
    assert Address.parse(address.float_str()) == address


def test_distance_weights_coarse_segments_more():
    base = Address.of(2, 3, 1, 4, 1)
    near = Address.of(2, 3, 1, 4, 2)          # differs in the last digit
    far = Address.of(3, 3, 1, 4, 1)           # differs in the first
    assert base.distance(near) == pytest.approx(0.5)
    assert base.distance(far) == pytest.approx(8.0)
    assert base.distance(near) < base.distance(far)


def test_distance_is_symmetric_and_zero_on_self():
    a, b = Address.of(1, 2, 3, 4, 5), Address.of(2, 1, 4, 3, 6)
    assert a.distance(b) == b.distance(a)
    assert a.distance(a) == 0.0


def test_similarity_falls_off_without_a_ceiling():
    base = Address.of(1, 1, 3, 1, 1)
    assert base.similarity(base) == 1.0
    assert 0.6 < base.similarity(Address.of(1, 1, 3, 1, 2)) < 0.7
    assert base.similarity(Address.of(2, 1, 3, 1, 1)) < 0.2


def test_is_under_and_parent():
    child = Address.of(1, 1, 3, 1, 4)
    assert child.is_under(Address.of(1))
    assert child.is_under(Address.of(1, 1, 3))
    assert not child.is_under(Address.of(2))
    assert child.parent() == Address.of(1, 1, 3, 1)
    assert child.siblings() == Address.of(1, 1, 3, 1)


def test_nearest_orders_by_distance():
    base = Address.of(1, 1, 3, 1, 1)
    others = [Address.of(2, 1, 3, 1, 1), Address.of(1, 1, 3, 1, 2), Address.of(1, 1, 3, 2, 1)]
    nearest = base.nearest(others, k=2)
    assert nearest[0][1] == Address.of(1, 1, 3, 1, 2)
    assert nearest[1][1] == Address.of(1, 1, 3, 2, 1)


def test_compose_and_spread():
    address = compose(2, 3, 1, 4, 17)
    assert address.segments == (2, 3, 1, 4, 17)
    assert address.domain == "code"
    assert address.kind == "module"
    summary = spread([address, compose(2, 3, 3, 1, 1), compose(1, 1, 3, 1, 1)])
    assert summary["count"] == 3
    assert summary["domains"]["code"] == 2
    assert summary["domains"]["prose"] == 1


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------

def test_rules_load_with_every_level_populated():
    rules = load_rules()
    assert len(rules) > 40
    counts = rules.level_counts()
    for level in LEVELS:
        assert counts[level] > 0, f"level {level} has no rules"
    assert rules.check() == []


def test_lowest_level_wins():
    rules = load_rules()
    decision = rules.decide("licence.allowed")
    assert decision.rule is not None
    assert decision.level == 1
    assert "MIT" in decision.value


def test_blocked_licence_is_a_refusal():
    rules = load_rules()
    blocked = rules.get("licence.blocked")
    assert "AGPL-3.0" in blocked
    assert rules.decide("licence.blocked").action == "block"


def test_unknown_key_has_no_rule():
    rules = load_rules()
    decision = rules.decide("nothing.speaks.to.this")
    assert decision.rule is None
    assert decision.action == "allow"
    assert rules.get("nothing.speaks.to.this", default=42) == 42


def test_trace_explains_the_decision():
    rules = load_rules()
    decision = rules.decide("licence.allowed")
    payload = decision.as_dict()
    assert payload["winner"]["rationale"]
    assert payload["winner"]["level"] == 1


def test_level_one_rules_never_prefer():
    rules = load_rules()
    for rule in rules:
        if rule.level == 1:
            assert rule.action in {"block", "allow", "warn"}, rule.id


# --------------------------------------------------------------------------
# languages
# --------------------------------------------------------------------------

def test_registry_has_both_kinds():
    registry = load_registry()
    assert len(registry.natural()) >= 5
    assert len(registry.code_languages()) >= 10
    assert registry.get("en").family_id == 1


def test_extension_mapping():
    registry = load_registry()
    assert registry.for_path("a/b.py").code == "python"
    assert registry.for_path("x.tsx").code == "typescript"
    assert registry.for_path("README.md").code == "markdown"
    assert registry.for_path("noext") is None


def test_detection_by_script_and_markers():
    registry = load_registry()
    assert registry.detect(
        "The Reserve Bank of India said the repo rate is unchanged and inflation is falling."
    )[0] == "en"
    assert registry.detect(
        "भारतीय रिज़र्व बैंक ने कहा कि रेपो दर में कोई बदलाव नहीं किया गया है और महंगाई कम हो रही है।"
    )[0] == "hi"
    assert registry.detect(
        "இந்திய ரிசர்வ் வங்கி வட்டி விகிதத்தில் மாற்றம் இல்லை என்று கூறியது."
    )[0] == "ta"


def test_unknown_language_raises():
    with pytest.raises(LanguageError):
        load_registry().get("xx")
