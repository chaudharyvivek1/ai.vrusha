"""Formulae as formulae: canonical form, extraction, and search.

The failures worth testing are the ones that make two spellings of one formula
look different — `b+a` against `a+b`, `\\left(x\\right)` against `(x)`, `x²`
against `x^2` — because a search that misses those is a search that only works
when the user already knows the exact source text.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.cli import main
from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.store import records
from vrusha.store.db import Store
from vrusha.store.mathquery import (canonical, extract_math, looks_like_math,
                                    match, plain, subformulas, symbols, to_latex)

FIXTURES = os.path.join(ROOT, "examples", "fixtures", "tables")
EULER = os.path.join(FIXTURES, "euler.html")


def _cfg(root):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(root)
    ensure_dirs(cfg)
    return cfg


# ---------------------------------------------------------------------------
# canonical form: two spellings, one string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("left,right", [
    (r"\left( x \right)", "(x)"),
    (r"b+a", "a+b"),
    (r"\frac{1}{2}", "(1)/(2)"),
    (r"a \cdot b", r"a\cdot b"),
    (r"\sin^2\theta + \cos^2\theta = 1", r"\cos^2\theta+\sin^2\theta=1"),
    ("∫₀¹ x² dx", r"\int_0^1 x^2 dx"),
    (r"x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}", r"x=(-b\pm sqrt(b^2-4ac))/(2a)"),
])
def test_two_spellings_canonicalise_the_same(left, right):
    assert canonical(left) == canonical(right)


def test_commutative_order_is_applied_per_side_of_an_equals():
    """`=` binds looser than `+`: sorting the whole thing invents a formula."""
    form = canonical(r"\sin^2\theta + \cos^2\theta = 1")
    assert form.count("=") == 1
    left, right = form.split("=")
    assert right == "1"
    assert left == "cos^2theta+sin^2theta"


def test_brackets_stay_balanced():
    form = canonical(r"\frac{-b \pm \sqrt{b^2-4ac}}{2a}")
    assert form.count("(") == form.count(")")
    assert "frac" not in form, "an unparsed \\frac leaves a token that matches everything"


def test_unicode_becomes_latex():
    assert to_latex("∫₀¹ x² dx").startswith(r"\int_0^1")
    assert to_latex("α+β") == r"\alpha+\beta"
    assert "²" not in to_latex("x²")


def test_plain_is_readable_for_a_snippet():
    text = plain(r"\frac{a}{b} + \pi")
    assert "frac" in text and "pi" in text and "{" not in text


def test_symbols_and_subformulas():
    form = canonical(r"x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}")
    found = symbols(form)
    assert "b" in found and "4" in found and "^" in found
    parts = subformulas(form)
    assert any("b^2-4ac" in part for part in parts)


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def test_delimiters_and_mathml_are_both_read():
    html = (r'<p>\(e^{i\pi}+1=0\)</p>'
            r'<p>$$x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}$$</p>'
            '<math><mi>a</mi><mo>+</mo><mi>b</mi></math>')
    equations = extract_math(html, "page")
    assert len(equations) == 3
    assert any(item.canonical == "1+e^(ipi)=0" for item in equations)
    assert any(item.display for item in equations)


def test_a_page_with_no_maths_yields_nothing():
    assert extract_math("<p>Plans renew annually.</p>", "p") == []
    assert extract_math("", "p") == []


def test_duplicate_formulae_are_stored_once():
    html = r"<p>\(a+b\)</p><p>\(b+a\)</p><p>\(\left(a+b\right)\)</p>"
    assert len(extract_math(html, "p")) == 1


def test_a_price_in_dollars_is_not_a_formula():
    """`$5` and `$10` are delimiters by accident; a single character is refused."""
    assert extract_math("<p>Costs $5 and $10.</p>", "p") == []


def test_looks_like_math():
    assert looks_like_math(r"e^{i\pi}+1=0")
    assert looks_like_math("∫ x dx")
    assert not looks_like_math("what is an address")
    assert not looks_like_math("")


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def _equation(latex: str):
    from vrusha.store.mathquery import Equation
    form = canonical(latex)
    return Equation(latex=latex, canonical=form, plain=plain(latex),
                    symbols=symbols(form))


def test_exact_beats_subformula_beats_symbols():
    quadratic = _equation(r"x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}")
    assert match(r"x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}", quadratic).kind == "exact"
    assert match("b^2-4ac", quadratic).kind == "subformula"
    loose = match("x b a c", quadratic)
    assert loose is None or loose.kind == "symbols"


def test_unrelated_query_does_not_match():
    assert match("k^2 + m^2 = q^2", _equation("a+b")) is None


# ---------------------------------------------------------------------------
# through the store
# ---------------------------------------------------------------------------


def test_formulae_are_stored_with_their_document_and_licence(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    result = records.extract_path(store, EULER, licence="MIT", verbose=False)
    assert result["equations"] == 3

    hits = records.search_equations(store, r"e^{i\pi}+1=0")
    assert hits and hits[0]["kind"] == "exact"
    assert hits[0]["licence"] == "MIT"
    assert hits[0]["url"].endswith("euler.html")


def test_a_subformula_query_finds_the_whole_formula(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    records.extract_path(store, EULER, licence="MIT", verbose=False)
    hits = records.search_equations(store, "b^2-4ac")
    assert hits
    assert hits[0]["kind"] == "subformula"
    assert "sqrt" in hits[0]["canonical"] or "b^2-4ac" in hits[0]["canonical"]


def test_re_extracting_does_not_double_the_formulae(tmp_path):
    store = Store(resolve(_cfg(tmp_path), "db"))
    records.extract_path(store, EULER, licence="MIT", verbose=False)
    records.extract_path(store, EULER, licence="MIT", verbose=False)
    total = store.conn.execute("SELECT COUNT(*) FROM x_equations").fetchone()[0]
    assert total == 3


def test_cli_math_finds_a_formula_with_its_source(tmp_path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "extract", "--dir", EULER, "--licence", "MIT"]) == 0
    capsys.readouterr()

    assert main(["--root", root, "math", r"e^{i\pi}+1=0"]) == 0
    out = capsys.readouterr().out
    assert "exact" in out and "euler.html" in out and "MIT" in out


def test_cli_math_reports_no_match_with_exit_1(tmp_path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "extract", "--dir", EULER, "--licence", "MIT"]) == 0
    capsys.readouterr()
    assert main(["--root", root, "math", "q^7 + z^9"]) == 1
    assert "no formula matched" in capsys.readouterr().out


def test_cli_math_without_a_query_lists_what_is_stored(tmp_path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "extract", "--dir", EULER, "--licence", "MIT"]) == 0
    capsys.readouterr()
    assert main(["--root", root, "math"]) == 0
    out = capsys.readouterr().out
    assert "a^2+b^2=c^2" in out
