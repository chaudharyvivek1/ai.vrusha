"""Math: find formulae on a page, canonicalise them, and search them as formulae.

A search box that cannot see `x²` is a search box for prose. Formulae are the
part of a technical page that a tokeniser destroys:

    a + b   and   b + a        the same expression, two different strings
    \\frac{1}{2}   and   0.5     never meet
    \\left( x \\right)  and  (x)  differ by a rendering command

The published systems agree on the shape of the answer — MathWebSearch indexes
Content MathML with substitution trees, MIaS canonicalises and reorders
commutative operands and indexes every subformula, PyA0 does substructure
matching in C. All three: **canonicalise, then index subformulas, then match
structurally.**

This module is the first two steps, in ~300 lines and no dependencies:

    extract_math(html)     <math>, \\( \\), \\[ \\], $$ $$, $ $  ->  LaTeX
    canonical(latex)       one string per formula, modulo commutative order
    tokens(canonical)      the symbols, and the subformulas worth matching

What it does not do: LaTeX -> MathML (a parser and a half), and semantic
equivalence beyond reordering (`\\sin^2+\\cos^2=1` and `1=\\sin^2+\\cos^2` are the
same fact and different formulae). That is where a weekend becomes PyA0, which
is why PyA0 belongs behind the router as an optional backend, not in here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

#: Symbols that arrive as characters and mean something in LaTeX. A formula
#: copied from a rendered page is mostly these.
_UNICODE = {
    "∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint",
    "∑": r"\sum", "∏": r"\prod", "√": r"\sqrt", "∞": r"\infty",
    "∂": r"\partial", "∇": r"\nabla", "±": r"\pm", "∓": r"\mp",
    "×": r"\times", "÷": r"\div", "·": r"\cdot", "∘": r"\circ",
    "≤": r"\le", "≥": r"\ge", "≠": r"\ne", "≈": r"\approx", "≡": r"\equiv",
    "∼": r"\sim", "∝": r"\propto", "∈": r"\in", "∉": r"\notin",
    "⊂": r"\subset", "⊃": r"\supset", "⊆": r"\subseteq", "⊇": r"\supseteq",
    "∪": r"\cup", "∩": r"\cap", "∅": r"\emptyset", "∀": r"\forall",
    "∃": r"\exists", "¬": r"\neg", "∧": r"\wedge", "∨": r"\vee",
    "→": r"\to", "←": r"\leftarrow", "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow", "⇐": r"\Leftarrow", "⇔": r"\Leftrightarrow",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho", "σ": r"\sigma",
    "τ": r"\tau", "υ": r"\upsilon", "φ": r"\phi", "χ": r"\chi",
    "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta",
    "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi",
    "Σ": r"\Sigma", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
    "′": "'", "″": "''", "…": r"\ldots", "⋯": r"\cdots", "ℏ": r"\hbar",
    "ℓ": r"\ell", "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}",
}

#: Superscripts and subscripts as characters, which is how a formula arrives when
#: it was copied from a rendered page: `x²` is `x^2` and `∫₀¹` is `\int_0^1`.
_SUPERSCRIPTS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                 "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
                 "⁺": "+", "⁻": "-", "⁼": "=", "⁽": "(", "⁾": ")",
                 "ⁿ": "n", "ⁱ": "i"}
_SUBSCRIPTS = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
               "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
               "₊": "+", "₋": "-", "₌": "=", "₍": "(", "₎": ")",
               "ₐ": "a", "ₑ": "e", "ₒ": "o", "ₓ": "x", "ₔ": "e",
               "ₕ": "h", "ₖ": "k", "ₗ": "l", "ₘ": "m", "ₙ": "n",
               "ₚ": "p", "ₛ": "s", "ₜ": "t"}

#: Commands that change how something is drawn, not what it is. Removing them
#: is what makes `\left(x\right)` and `(x)` the same formula.
_NOISE = re.compile(
    r"\\(left|right|big|Big|bigg|Bigg|displaystyle|textstyle|scriptstyle|"
    r"limits|nolimits|!|,|;|:|quad|qquad|enspace|thinspace)(?![A-Za-z])")

_SPACING = re.compile(r"\\[ ,;:!]")

#: `\mathrm{d}x` and `dx` are the same thing to a search box.
_UPRIGHT = re.compile(r"\\(mathrm|text|operatorname|mathit|mathbf)\s*\{([^{}]*)\}")

_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_DELIMITED = re.compile(
    r"\$\$(?P<display>.+?)\$\$"
    r"|\\\[(?P<bracket>.+?)\\\]"
    r"|\\\((?P<paren>(?:(?!\\[()]).)+?)\\\)"
    r"|(?<![\\$])\$(?P<inline>[^$\n]{2,200}?)\$(?!\$)",
    re.DOTALL)

#: What an inline `$...$` has to look like to be maths. `$5 and $10` is a price
#: list, and TeX itself refuses a span that starts or ends with a space.
_INLINE_OPERATORS = set("+-*/=^_<>")


def _inline_is_math(value: str) -> bool:
    text = value.strip()
    if len(text) < 2:
        return False
    if text != value:
        return False                    # `$ x $` is not a formula in TeX either
    if "\\" in text:
        return True                     # a command: \alpha, \frac{1}{2}
    if any(char in _INLINE_OPERATORS for char in text):
        return True                     # an operator: a+b, x^2, f=ma
    return " " not in text              # a bare expression: abc, x1

#: A page can hold a hundred formulae; a table of contents is not a corpus.
MAX_EQUATIONS = 200


@dataclass
class Equation:
    latex: str
    canonical: str
    plain: str = ""
    symbols: List[str] = field(default_factory=list)
    source: str = ""
    display: bool = False


# ---------------------------------------------------------------------------
# canonical form
# ---------------------------------------------------------------------------


def to_latex(text: str) -> str:
    """Unicode maths to LaTeX. A formula copied off a rendered page is unicode."""
    if not text:
        return ""
    out: List[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in _SUPERSCRIPTS or char in _SUBSCRIPTS:
            table = _SUPERSCRIPTS if char in _SUPERSCRIPTS else _SUBSCRIPTS
            marker = "^" if char in _SUPERSCRIPTS else "_"
            run = []
            while index < len(text) and text[index] in table:
                run.append(table[text[index]])
                index += 1
            body = "".join(run)
            out.append(f"{marker}{body}" if len(body) == 1 else f"{marker}{{{body}}}")
            continue
        out.append(_UNICODE.get(char, char))
        index += 1
    return "".join(out)


def _read_group(text: str, start: int) -> tuple:
    """The contents of the brace group beginning at `start`, and where it ends."""
    if start >= len(text) or text[start] != "{":
        return None, start
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return None, start


def _expand_commands(text: str) -> str:
    r"""`\frac{a}{b}` -> `(a)/(b)` and `\sqrt{a}` -> `sqrt(a)`, nested or not.

    A regex cannot do this: `\frac{-b \pm \sqrt{b^2-4ac}}{2a}` has braces inside
    the numerator, and a `[^{}]*` pattern gives up and leaves the command in the
    canonical form, where `frac` becomes a symbol that matches everything.
    """
    for _ in range(50):
        index = text.find("\\frac")
        if index >= 0:
            first, after_first = _read_group(text, index + 5)
            if first is None:
                break
            second, after_second = _read_group(text, after_first)
            if second is None:
                break
            text = text[:index] + f"({first})/({second})" + text[after_second:]
            continue
        index = text.find("\\dfrac")
        if index >= 0:
            text = text[:index] + "\\frac" + text[index + 6:]
            continue
        index = text.find("\\sqrt")
        if index >= 0:
            first, after_first = _read_group(text, index + 5)
            if first is None:
                break
            text = text[:index] + f"sqrt({first})" + text[after_first:]
            continue
        break
    return text


def _strip_braces(expression: str) -> str:
    """`{x}` -> `x`, but `{x+1}` keeps its braces: they group."""
    def replace(match: re.Match) -> str:
        inner = match.group(1)
        if re.fullmatch(r"[A-Za-z0-9]+|\\[A-Za-z]+", inner.strip()):
            return inner
        return match.group(0)

    previous = None
    while previous != expression:
        previous = expression
        expression = re.sub(r"\{([^{}]*)\}", replace, expression)
    return expression


def _split_top_level(expression: str, operator: str) -> Optional[List[str]]:
    """Split on a top-level operator, or None if it does not appear there.

    `a+b+c` -> ['a','b','c'];  `a+(b*c)` -> None for '+', because the operator
    inside the parentheses is not top level.
    """
    parts, depth, current = [], 0, []
    for index, char in enumerate(expression):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        if char == operator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    if len(parts) == 1:
        return None
    return [part for part in parts if part.strip()]


def _order_commutative(expression: str) -> str:
    """`b+a` -> `a+b`, and the same for `*`. Recursively, inside brackets too.

    `=` binds looser than `+`, so the sides are ordered separately: sorting the
    whole expression turns `sin^2+cos^2=1` into `cos^2=1+sin^2`, which is a
    different formula and a bug that silently stops two spellings matching.
    """
    if "=" in expression:
        sides = expression.split("=")
        if len(sides) == 2:
            left, right = (_order_commutative(side) for side in sides)
            return f"{left}={right}"

    for operator in ("+", "*"):
        parts = _split_top_level(expression, operator)
        if parts and len(parts) > 1:
            ordered = sorted(_order_commutative(part) for part in parts)
            return operator.join(ordered)
    # recurse into the first bracketed group so `(b+a)*c` still normalises
    match = re.search(r"\(([^()]*)\)", expression)
    if match:
        inner = _order_commutative(match.group(1))
        return expression[:match.start(1)] + inner + expression[match.end(1):]
    return expression


def _strip_outer_parens(expression: str) -> str:
    """`(a+b)` -> `a+b`: parentheses around the whole formula are decoration.

    Only when the first bracket actually closes at the last character: in
    `(a)/(b)` the opening bracket closes early, and stripping would change the
    formula.
    """
    while expression.startswith("(") and expression.endswith(")"):
        depth = 0
        closes_at_end = False
        for index, char in enumerate(expression):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    closes_at_end = index == len(expression) - 1
                    break
        if not closes_at_end:
            break
        expression = expression[1:-1]
    return expression


def canonical(latex: str) -> str:
    """One string per formula, modulo rendering and commutative order."""
    if not latex:
        return ""
    text = to_latex(latex).strip()
    text = text.replace("$$", "").replace("$", "")
    text = _NOISE.sub("", text)
    text = _SPACING.sub("", text)
    text = _UPRIGHT.sub(lambda match: match.group(2), text)

    # \frac{a}{b} -> (a)/(b): the same expression as a/(b), written differently
    text = _expand_commands(text)

    text = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", "", text)
    text = re.sub(r"\s+", "", text)
    text = _strip_braces(text)
    text = text.replace("{", "(").replace("}", ")")
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)      # \sin -> sin, \alpha -> alpha
    text = _order_commutative(text)
    text = _strip_outer_parens(text)
    return text.strip()


def plain(latex: str) -> str:
    """A readable version, for full-text search and for a snippet in a result."""
    text = to_latex(latex)
    text = re.sub(r"\\([A-Za-z]+)", r" \1 ", text)
    text = re.sub(r"[{}$\\]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# symbols and subformulas
# ---------------------------------------------------------------------------

_IDENTIFIER = re.compile(r"[A-Za-z]+|\d+(?:\.\d+)?")
_OPERATORS = set("+-*/=<>^_()[]|")


def symbols(canonical_form: str) -> List[str]:
    """The symbols a formula is made of: identifiers, operators, numbers.

    This is what makes a partial match possible without a parser: two formulae
    that share most of their symbols are candidates, and the caller decides.
    """
    found = _IDENTIFIER.findall(canonical_form)
    found += [char for char in canonical_form if char in _OPERATORS]
    seen, ordered = set(), []
    for token in found:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


def subformulas(canonical_form: str, limit: int = 40) -> List[str]:
    """Every bracketed group inside the formula, innermost first.

    MIaS indexes subformulas so that a query for `b^2-4ac` finds a page whose
    formula is the whole quadratic solution. This is that idea, without the
    XML tree.
    """
    output: List[str] = []
    for match in re.finditer(r"\(([^()]*)\)", canonical_form):
        inner = match.group(1)
        if len(inner) >= 3 and inner not in output:
            output.append(inner)
    # and the sides of an equation, which are formulae in their own right
    for side in canonical_form.split("="):
        side = side.strip()
        if len(side) >= 4 and side not in output and side != canonical_form:
            output.append(side)
    return output[:limit]


# ---------------------------------------------------------------------------
# finding formulae on a page
# ---------------------------------------------------------------------------

_MATHML = re.compile(r"<math[^>]*>(.*?)</math>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def _from_mathml(inner: str) -> str:
    """MathML to LaTeX, for the small subset that matters: text and structure.

    A full conversion is a project. This keeps the identifiers, the operators
    and the numbers, which is what a search needs, and gives up on layout.
    """
    text = _TAG.sub(" ", inner)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_math(html: str, source: str = "") -> List[Equation]:
    """Every formula on a page, canonicalised and deduplicated."""
    if not html:
        return []
    found: List[Equation] = []
    seen: Set[str] = set()

    def add(latex: str, display: bool) -> None:
        latex = (latex or "").strip()
        if len(latex) < 2 or len(latex) > 4000:
            return
        form = canonical(latex)
        if len(form) < 2 or form in seen:
            return
        seen.add(form)
        found.append(Equation(latex=latex, canonical=form, plain=plain(latex),
                              symbols=symbols(form), source=source,
                              display=display))

    for match in _MATHML.finditer(html):
        add(_from_mathml(match.group(1)), True)

    # strip the MathML so its text is not read again as delimited maths
    without_mathml = _MATHML.sub(" ", html)
    for match in _DELIMITED.finditer(without_mathml):
        for name in ("display", "bracket", "paren", "inline"):
            value = match.group(name)
            if value:
                if name == "inline" and not _inline_is_math(value):
                    break
                add(value, display=name in ("display", "bracket"))
                break
        if len(found) >= MAX_EQUATIONS:
            break

    return found[:MAX_EQUATIONS]


def looks_like_math(text: str) -> bool:
    """Does this query contain a formula rather than only words?

    Used by `vrusha math` to decide whether to canonicalise the query or to
    treat it as a text search over formula names.
    """
    if not text:
        return False
    if any(char in text for char in "∫∑∏√∞≤≥≠≈∈⊂∂∇"):
        return True
    if re.search(r"\$|\\\(|\\\[|\\[A-Za-z]{2,}", text):
        return True
    if re.search(r"[A-Za-z0-9)\]]\s*[=+*/^]\s*[A-Za-z0-9(\[\\]", text):
        return True
    return False


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


@dataclass
class Match:
    equation: Equation
    score: float
    kind: str            # exact | subformula | symbols


def match(query: str, equation: Equation, *, min_overlap: float = 0.5) -> Optional[Match]:
    """How this formula matches the query, or None.

    Exact first: a canonical match is the formula. Then a subformula: the query
    is part of a larger expression, which is what a search for `b^2-4ac` wants.
    Then symbols: most of the query's symbols appear, which is a candidate for a
    human to read rather than an answer.
    """
    query_form = canonical(query)
    if not query_form:
        return None
    if query_form == equation.canonical:
        return Match(equation=equation, score=1.0, kind="exact")
    if query_form in equation.canonical or equation.canonical in query_form:
        return Match(equation=equation, score=0.9, kind="subformula")
    for part in subformulas(equation.canonical):
        if query_form and query_form in part:
            return Match(equation=equation, score=0.85, kind="subformula")

    wanted = set(symbols(query_form))
    if not wanted:
        return None
    have = set(equation.symbols)
    overlap = len(wanted & have) / len(wanted)
    if overlap >= min_overlap:
        return Match(equation=equation, score=round(0.5 * overlap, 3), kind="symbols")
    return None
