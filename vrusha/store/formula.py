"""Formulas: a derived column computed from other columns, safely.

A price per square foot is `price / area`. That is arithmetic on values that
came from a web page, so the question is not "can Python do this" — it can —
but "what else can this string do". `__import__('os').system(...)` is also
arithmetic-shaped.

So the expression is never handed to `eval`. It is parsed with `ast`, walked
against a whitelist, and interpreted by this module. Anything not on the list
is refused with the node that broke it, before a single row is touched.

What is allowed:

    numbers, strings, True/False/None
    column names
    + - * / // % **  and unary - +
    == != < <= > >=  and  and / or / not
    a if b else c
    min max round abs sum len if_null date_diff

What is not, and why it matters:

    attribute access   `x.__class__` walks to the type, and from there to
                       everything else in the process
    subscripts         `os.environ['X']`, `mapping[key]`
    comprehensions     a loop is a program, and a program needs a sandbox
    lambdas            same
    any other call     `open`, `eval`, `exec`, `__import__`, `compile`

A formula that fails the walk raises `FormulaError` naming the node, and the
CLI prints it. Nothing is evaluated, nothing is written.
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Set


class FormulaError(ValueError):
    """The expression is not allowed, or refers to something that is not there."""


# ---------------------------------------------------------------------------
# the whitelist
# ---------------------------------------------------------------------------

_BINOPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_CMPOPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

_UNARYOPS: Dict[type, Callable[[Any], Any]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
}

_FORBIDDEN = (
    ast.Attribute, ast.Subscript, ast.Lambda, ast.ListComp, ast.SetComp,
    ast.DictComp, ast.GeneratorExp, ast.Await, ast.Yield, ast.YieldFrom,
    ast.NamedExpr, ast.Starred, ast.Delete, ast.Global, ast.Nonlocal,
    ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith, ast.Try,
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Return,
    ast.For, ast.AsyncFor, ast.While, ast.If, ast.Raise, ast.Assert,
    ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Pass, ast.Break, ast.Continue,
)


def _if_null(value: Any, fallback: Any = 0) -> Any:
    return fallback if value is None or value == "" else value


def _date_diff(later: Any, earlier: Any, unit: str = "days") -> Optional[float]:
    """Days (or months/years) between two ISO dates. None if either is missing."""
    def parse(value: Any) -> Optional[date]:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if not isinstance(value, str) or not value:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:len(fmt) + 2], fmt).date()
            except ValueError:
                continue
        return None

    left, right = parse(later), parse(earlier)
    if left is None or right is None:
        return None
    days = (left - right).days
    if unit in ("days", "day", "d"):
        return float(days)
    if unit in ("months", "month", "m"):
        return round(days / 30.4375, 3)
    if unit in ("years", "year", "y"):
        return round(days / 365.25, 4)
    raise FormulaError(f"date_diff: unknown unit {unit!r}; use days, months or years")


#: name -> (function, arity). Arity is checked before the call, so `min()` with
#: no arguments is a formula error rather than a TypeError halfway through a
#: table.
FUNCTIONS: Dict[str, tuple] = {
    "min": (min, (1, 64)),
    "max": (max, (1, 64)),
    "round": (round, (1, 2)),
    "abs": (abs, (1, 1)),
    "sum": (sum, (1, 64)),
    "len": (len, (1, 1)),
    "if_null": (_if_null, (1, 2)),
    "date_diff": (_date_diff, (2, 3)),
}

CONSTANTS: Dict[str, Any] = {"true": True, "false": False, "none": None,
                             "True": True, "False": False, "None": None}


# ---------------------------------------------------------------------------
# the evaluator
# ---------------------------------------------------------------------------


@dataclass
class Formula:
    """A checked expression. Constructing one is the security boundary."""

    source: str
    tree: ast.Expression
    names: Set[str] = field(default_factory=set)
    calls: Set[str] = field(default_factory=set)

    @classmethod
    def parse(cls, source: str, columns: Sequence[str] = ()) -> "Formula":
        if not source or not source.strip():
            raise FormulaError("empty formula")
        if len(source) > 400:
            raise FormulaError("formula longer than 400 characters")
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise FormulaError(f"not an expression: {exc.msg}") from exc

        known = set(columns)
        names: Set[str] = set()
        calls: Set[str] = set()
        # a function name is a Name node too, and must not be read as a column
        function_names = {id(node.func) for node in ast.walk(tree)
                          if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Name)}

        for node in ast.walk(tree):
            if isinstance(node, _FORBIDDEN):
                raise FormulaError(
                    f"{type(node).__name__} is not allowed in a formula "
                    f"({_describe(node)}); formulas are arithmetic over columns")
            if isinstance(node, ast.Name):
                if id(node) in function_names:
                    continue
                if node.id.startswith("__"):
                    raise FormulaError(f"{node.id!r} is not allowed")
                if node.id in CONSTANTS:
                    continue
                if known and node.id not in known:
                    raise FormulaError(
                        f"unknown column {node.id!r}; this table has "
                        f"{', '.join(sorted(known)) or 'no columns'}")
                names.add(node.id)
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    raise FormulaError(
                        "only named functions may be called, not "
                        f"{type(node.func).__name__}")
                name = node.func.id
                if name not in FUNCTIONS:
                    raise FormulaError(
                        f"{name!r} is not a formula function; available: "
                        f"{', '.join(sorted(FUNCTIONS))}")
                low, high = FUNCTIONS[name][1]
                if not low <= len(node.args) <= high:
                    raise FormulaError(f"{name}() takes {low}-{high} arguments, "
                                       f"got {len(node.args)}")
                if node.keywords and name != "date_diff":
                    raise FormulaError(f"{name}() takes no keyword arguments")
                calls.add(name)
            if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                # literals are fine; a comprehension is not, and is caught above
                continue
            if isinstance(node, ast.Constant):
                if not isinstance(node.value, (int, float, str, bool, type(None))):
                    raise FormulaError(
                        f"{type(node.value).__name__} literals are not allowed")

        return cls(source=source.strip(), tree=tree, names=names, calls=calls)

    # -- evaluation --------------------------------------------------------

    def evaluate(self, row: Dict[str, Any]) -> Any:
        return self._eval(self.tree.body, row)

    def _eval(self, node: ast.AST, row: Dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            return row.get(node.id)
        if isinstance(node, ast.BinOp):
            left = self._eval(node.left, row)
            right = self._eval(node.right, row)
            if left is None or right is None:
                return None                      # SQL semantics, not a crash
            operation = _BINOPS[type(node.op)]
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                return None                      # divide by zero is a null
            return operation(left, right)
        if isinstance(node, ast.UnaryOp):
            return _UNARYOPS[type(node.op)](self._eval(node.operand, row))
        if isinstance(node, ast.BoolOp):
            values = [self._eval(value, row) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, row)
            for operation, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator, row)
                if left is None or right is None:
                    return False
                if not _CMPOPS[type(operation)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test, row)
                              else node.orelse, row)
        if isinstance(node, ast.Call):
            name = node.func.id
            function = FUNCTIONS[name][0]
            args = [self._eval(arg, row) for arg in node.args]
            kwargs = {kw.arg: self._eval(kw.value, row) for kw in node.keywords}
            if any(arg is None for arg in args) and name != "if_null":
                return None
            try:
                return function(*args, **kwargs)
            except (TypeError, ValueError) as exc:
                raise FormulaError(f"{name}() failed on {args}: {exc}") from exc
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self._eval(item, row) for item in node.elts]
        raise FormulaError(f"{type(node).__name__} is not allowed")


def _describe(node: ast.AST) -> str:
    try:
        return ast.unparse(node)[:60]
    except Exception:                        # noqa: BLE001
        return type(node).__name__


# ---------------------------------------------------------------------------
# a spec: one or more derived columns
# ---------------------------------------------------------------------------


@dataclass
class Derived:
    name: str
    expr: str
    type: str = "float"
    guards: List[str] = field(default_factory=list)
    formula: Optional[Formula] = None
    guard_formulas: List[Formula] = field(default_factory=list)

    def compile(self, columns: Sequence[str]) -> "Derived":
        self.formula = Formula.parse(self.expr, columns)
        self.guard_formulas = [Formula.parse(guard, columns) for guard in self.guards]
        return self

    def compute(self, row: Dict[str, Any]) -> Any:
        """The value, or None when a guard says this row is not computable."""
        for guard in self.guard_formulas:
            if not guard.evaluate(row):
                return None
        return self.formula.evaluate(row)


def compile_spec(spec: Dict[str, Any], columns: Sequence[str]) -> List[Derived]:
    """`{"derived": [{"name": ..., "expr": ..., "guards": [...]}]}` -> compiled.

    Every formula is compiled before any row is read: a bad spec fails once, at
    the top, rather than halfway through a table with half the rows written.
    """
    items = spec.get("derived") or []
    if not isinstance(items, list) or not items:
        raise FormulaError('spec needs {"derived": [{name, expr}, ...]}')
    output: List[Derived] = []
    seen: Set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            raise FormulaError("every derived column needs a name")
        if not name.replace("_", "").isalnum():
            raise FormulaError(f"column name {name!r} must be alphanumeric with _")
        if name in seen:
            raise FormulaError(f"duplicate derived column {name!r}")
        seen.add(name)
        derived = Derived(name=name, expr=str(item.get("expr") or ""),
                          type=str(item.get("type") or "float"),
                          guards=[str(g) for g in (item.get("guards") or [])])
        output.append(derived.compile(columns))
    return output
