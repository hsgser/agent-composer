"""`${...}` reference resolution + the `when:` boolean expression evaluator.

Ported from the legacy engine's lark grammar, with one change: variable
resolution now goes through `TypedVariablePool.resolve`, so it reads typed
segments (and can traverse into object outputs — `${x.output.output.ratio}` —
which the old dict-of-str pool could not).

Grammar: comparisons (`==` `!=` `<` `<=` `>` `>=` `in` `not in`) over `${...}`
references and literals, combined with `and` / `or` / `not` and parentheses.
A bare reference with no comparison operator is rejected.
"""

import re
from enum import Enum
from typing import Any, Callable

from lark import Lark, Token, Transformer, Tree
from lark.exceptions import LarkError, VisitError

from agent_composer.state.pool import TypedVariablePool


class ExpressionError(ValueError):
    """A `when:` expression could not be parsed or evaluated."""


_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


def resolve_reference(path: str, pool: TypedVariablePool) -> Any:
    """Resolve a `${path}` reference against the pool (missing -> None)."""
    parts = [p for p in path.strip().split(".")]
    if not parts or any(not p for p in parts):
        raise ExpressionError(f"invalid variable path: '${{{path}}}'")
    head, *rest = parts
    return pool.resolve(head, rest)


def _resolve_in_record(path: str, record: dict) -> Any:
    """Resolve a `${path}` against a node's bound input record (dotted walk).

    A missing input / dotted miss / None step -> None: the LOCKED `when:` missing->falsy
    contract (the compile-time strict check rejects undeclared names, so a None here is a
    legitimate empty value, not an authoring error). Bare local-input refs only.
    """
    parts = [p.strip() for p in path.strip().split(".")]
    if not parts or any(not p for p in parts):
        raise ExpressionError(f"invalid variable path: '${{{path}}}'")
    value: Any = record.get(parts[0])
    for step in parts[1:]:
        value = value.get(step) if isinstance(value, dict) else None
    return value


def render_template(text: str, pool: TypedVariablePool) -> str:
    """Substitute every `${ref}` in `text` with its resolved value (str), against
    the whole pool (`node`/`system` namespaces).

    No production caller as of slice 5 — strict AGENT renders prompts against the
    bound input record via `render_template_record`. Retained (and test-pinned)
    for the open-namespace render path a future templated `HUMAN_INPUT` prompt
    will need. An UNRESOLVED reference (resolves to None) RAISES `ExpressionError`
    rather than silently rendering "" (the runtime floor for refs the compile-time
    check can't prove total).
    """

    def _sub(match: "re.Match[str]") -> str:
        ref = match.group(1)
        value = resolve_reference(ref, pool)
        if value is None:
            raise ExpressionError(f"unresolved reference ${{{ref}}} in template")
        return str(value)

    return _VAR_RE.sub(_sub, text)


def _parse_literal(token: str) -> Any:
    t = token.strip()
    if not t:
        raise ExpressionError("empty literal")
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    if _NUMBER_RE.match(t):
        return float(t) if "." in t else int(t)
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        return t[1:-1]
    raise ExpressionError(f"cannot parse literal {token!r}")


def _arith(op: str, lhs: Any, rhs: Any) -> Any:
    """A binary arithmetic op over NUMBERS ONLY. A non-numeric operand —
    string/bool/null/None — is a loud `ExpressionError` (bool is excluded: it is
    `type bool`, not `int`)."""
    if type(lhs) not in (int, float) or type(rhs) not in (int, float):
        raise ExpressionError(f"arithmetic `{op}` requires numbers, got {lhs!r} {op} {rhs!r}")
    try:
        if op == "+":
            return lhs + rhs
        if op == "-":
            return lhs - rhs
        if op == "*":
            return lhs * rhs
        if op == "/":
            return lhs / rhs
        if op == "%":
            return lhs % rhs
    except ZeroDivisionError as exc:
        raise ExpressionError(f"division by zero: {lhs!r} {op} {rhs!r}") from exc
    raise ExpressionError(f"unsupported arithmetic operator: {op!r}")


def _eval_comparison(op: str, lhs: Any, rhs: Any) -> bool:
    try:
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        if op in ("in", "not in"):
            try:
                contained = lhs in rhs
            except TypeError as exc:
                raise ExpressionError(f"`in` rhs is not iterable: {exc}") from exc
            return contained if op == "in" else not contained
        # ordered comparisons: None on either side -> False (propagate falsy)
        if lhs is None or rhs is None:
            return False
        if op == "<":
            return lhs < rhs
        if op == "<=":
            return lhs <= rhs
        if op == ">":
            return lhs > rhs
        if op == ">=":
            return lhs >= rhs
    except TypeError as exc:
        raise ExpressionError(f"type error evaluating {lhs!r} {op} {rhs!r}: {exc}") from exc
    raise ExpressionError(f"unsupported operator: {op!r}")


_GRAMMAR = r"""
?start: or_expr

?or_expr: and_expr (OR and_expr)*
?and_expr: not_expr (AND not_expr)*
?not_expr: NOT not_expr   -> negate
         | comparison
?comparison: sum
           | sum COMP_OP sum   -> compare
           | sum IN sum        -> compare_in
           | sum NOT_IN sum    -> compare_notin

// arithmetic: numbers only at eval; a single value bubbles up unchanged.
?sum: product
    | sum "+" product   -> add
    | sum "-" product   -> sub
?product: factor
        | product "*" factor   -> mul
        | product "/" factor   -> div
        | product "%" factor   -> mod
?factor: atom
       | "-" factor   -> neg
?atom: REF | NUMBER | STRING | BOOL | NULL
     | list_lit
     | "(" or_expr ")"

// list literal: a value (the `in [...]` / `!= []` RHS) — elements are literals/refs
// (seeds 05/10 use `[]` and `["alpha", ...]`). Nested arithmetic isn't needed here.
list_lit: "[" [or_expr ("," or_expr)*] "]"

COMP_OP: "==" | "!=" | "<=" | ">=" | "<" | ">"
REF: /\$\{[^}]+\}/
STRING: /"[^"]*"/ | /'[^']*'/
NUMBER: /\d+(\.\d+)?/

NOT_IN.5: /not\s+in\b/
IN.4: /in\b/
AND.4: /and\b/
OR.4: /or\b/
NOT.4: /not\b/
BOOL.3: /true\b/ | /false\b/
NULL.3: /null\b/ | /none\b/

%import common.WS
%ignore WS
"""


class _Evaluator(Transformer):
    """Transforms a parsed `when:`/`asserts:` tree bottom-up: terminals resolve to
    VALUES (a `${ref}` via the resolve callable, literals to themselves), arithmetic
    nodes compute numbers, comparisons/boolean ops fold to bools."""

    def __init__(self, resolve: Callable[[str], Any]):
        super().__init__()
        self._resolve = resolve

    # --- terminals -> values (bottom of the transform) --- #
    def REF(self, tok):
        return self._resolve(str(tok)[2:-1])

    def NUMBER(self, tok):
        s = str(tok)
        return float(s) if "." in s else int(s)

    def STRING(self, tok):
        return str(tok)[1:-1]

    def BOOL(self, tok):
        return str(tok).lower() == "true"

    def NULL(self, tok):
        return None

    def list_lit(self, items):
        # elements are already values (refs/literals transformed bottom-up); an empty
        # `[]` yields []. This is the `in [...]` / `!= []` RHS value (seeds 05/10).
        return list(items)

    # --- arithmetic (operands already values; inline ops filtered) --- #
    def add(self, items):
        return _arith("+", items[0], items[1])

    def sub(self, items):
        return _arith("-", items[0], items[1])

    def mul(self, items):
        return _arith("*", items[0], items[1])

    def div(self, items):
        return _arith("/", items[0], items[1])

    def mod(self, items):
        return _arith("%", items[0], items[1])

    def neg(self, items):
        v = items[0]
        if type(v) not in (int, float):
            raise ExpressionError(f"unary minus requires a number, got {v!r}")
        return -v

    # --- comparisons (operands already values; COMP_OP/IN/NOT_IN kept) --- #
    def compare(self, items):
        lhs, op, rhs = items
        return _eval_comparison(str(op), lhs, rhs)

    def compare_in(self, items):
        return _eval_comparison("in", items[0], items[2])

    def compare_notin(self, items):
        return _eval_comparison("not in", items[0], items[2])

    # --- boolean combinators --- #
    def negate(self, items):
        operand = [i for i in items if not isinstance(i, Token)]
        return not operand[-1]

    def or_expr(self, items):
        return any(i for i in items if isinstance(i, bool))

    def and_expr(self, items):
        return all(i for i in items if isinstance(i, bool))


_PARSER = Lark(_GRAMMAR, parser="lalr", maybe_placeholders=False)


def _evaluate(expression: str, resolve: Callable[[str], Any]) -> bool:
    """Parse + evaluate a `when:` expression, resolving each `${ref}` via `resolve`."""
    try:
        tree = _PARSER.parse(expression)
    except LarkError as exc:
        raise ExpressionError(
            f"could not parse `when:` expression {expression!r}: must be one or more "
            f"comparisons combined with and/or/not. {exc}"
        ) from exc
    try:
        result = _Evaluator(resolve).transform(tree)
    except VisitError as exc:
        if isinstance(exc.orig_exc, ExpressionError):
            raise exc.orig_exc from None
        raise ExpressionError(str(exc.orig_exc)) from exc.orig_exc
    if isinstance(result, bool):
        return result
    raise ExpressionError(f"expression {expression!r} did not evaluate to a boolean")


def evaluate_when(expression: str, pool: TypedVariablePool) -> bool:
    """
    Evaluate a `when:` boolean expression against the variable pool.

    Parses and folds the expression — comparisons (`==` `!=` `<` `<=` `>` `>=` `in`
    `not in`) over `${...}` references and literals, combined with `and` / `or` / `not`
    — to a single boolean. This is the pool-based path, kept for manifest parse-checks and
    the deferred LOOP `while:` predicate seam; strict `CASE` uses the record-based path.

    Args:
        expression (`str`):
            The `when:` source: one or more comparisons combined with `and`/`or`/`not`.
            A bare reference with no comparison operator is rejected.
        pool (`TypedVariablePool`):
            The variable pool each `${ref}` resolves against (`node`/`system` namespaces).
            A reference that resolves to `None` participates as a falsy value.

    Returns:
        `bool`:
            The truth value of the expression.

    Raises:
        `ExpressionError`:
            If the expression is malformed, references an invalid path, mixes
            incompatible types, or does not evaluate to a boolean.

    Example:
        ```python
        evaluate_when("${score.output} >= 0.5 and ${flag.output}", pool)
        ```
    """
    return _evaluate(expression, lambda path: resolve_reference(path, pool))


def first_failing_assert(exprs, pool: TypedVariablePool):
    """The first assert expression in `exprs` that is false against `pool`, else None.

    The shared enforcement primitive for a flow's `asserts:` (boundary or post): `run_flow`
    uses it at the top-level boundary, and the REF/MAP child seam uses it to enforce a def's
    child-boundary asserts. Each expr is the `when:`/`asserts:` boolean grammar."""
    for expr in exprs:
        if not evaluate_when(expr, pool):
            return expr
    return None


def evaluate_when_record(expression: str, record: dict) -> bool:
    """Evaluate a strict-CASE `when:` against the node's bound input record.

    `${name}` / `${name.path}` resolve against `record`; a miss -> None, which is falsy
    through ==/!=/ordered comparisons (the locked `when:` missing->falsy contract).
    Note: `in`/`not in` with a None operand still raises (unchanged from the pool path).
    Bare local-input refs only; pool namespaces are not in scope (they belong in the
    node's input `from:` bindings).

    cf. `render_template_record` (strict AGENT), which RAISES on the same dotted miss.
    `when:` deliberately stays falsy so `default` can fire — so a dotted-key typo on a
    declared dict input routes to `default` silently until the `types:` registry can
    validate dotted paths at compile time (the head is checked today, not the path).
    """
    return _evaluate(expression, lambda path: _resolve_in_record(path, record))


# --------------------------------------------------------------------------- #
# The ONE unified evaluator — walks a tree from `expr.grammar.parse_expr` against
# a `resolve` callable. This SUPERSEDES the three divergent `${...}` evaluators
# (binding coalesce/default in `template`, `when:`/`asserts:` boolean in `_Evaluator`
# above, prompt builtin-call in `template`). It is added ALONGSIDE the legacy paths;
# later steps switch the call sites over and delete the legacy code.
# --------------------------------------------------------------------------- #


class ResolveMode(Enum):
    """How a MISSING reference (one `resolve` maps to `None`) is treated.

    - `STRICT_RAISE`: a missing ref is an error — raise `ExpressionError`. For a
      context where every reference must be present (a strict render).
    - `BINDING_NONE`: a missing ref is `None` — the binding contract (a coalesce /
      default may then fire, or the whole value is `None`).
    - `CONDITION_FALSY`: a missing ref is `None`, which is falsy through comparisons
      (the locked `when:` missing->falsy contract). Same None as `BINDING_NONE`; the
      distinct name marks the intent at the call site (a `when:`/`asserts:` predicate).
    """

    STRICT_RAISE = "strict-raise"
    BINDING_NONE = "binding-none"
    CONDITION_FALSY = "condition-falsy"


# A sentinel telling a `refcall` miss apart from a resolved `None`, so `default`/
# `required` can fire on a genuine miss. Non-strict modes turn it into `None` at the
# operator boundary; strict mode raises.
class _Missing:
    """Marker for an unresolved reference (distinct from a resolved `None` value)."""


_MISSING = _Missing()


def _value_add(lhs: Any, rhs: Any) -> Any:
    """`+` on resolved VALUES (NOT the number-only `_arith` gate): so `str+str`
    concatenates and `list+list` extends. Arithmetic over `None` (a missing ref in a
    non-strict mode) propagates Python's `TypeError`, wrapped by the caller."""
    return lhs + rhs


class _EvalExpr:
    """Recursive walker for a `parse_expr` tree, parameterised by `resolve`, `item`,
    and `mode`. Hand-written (not a Lark `Transformer`) so coalesce / default /
    required can SHORT-CIRCUIT — evaluate the fallback only when the head misses.

    A `refcall` reference evaluates to `_MISSING` on an unresolved path; every value
    consumer (operators, comparisons, the top-level entry) maps `_MISSING` to `None`
    for non-strict modes or raises for `STRICT_RAISE`."""

    def __init__(self, resolve: Callable[[str], Any], item: Any, mode: ResolveMode):
        self._resolve = resolve
        self._item = item
        self._mode = mode

    # --- top-level entry: coerce a `_MISSING` head to a value per mode --- #
    def eval(self, node: "Tree | Token") -> Any:
        return self._as_value(self._walk(node))

    def _as_value(self, v: Any) -> Any:
        """Turn an internal `_MISSING` into a mode-appropriate value: `None` for the
        non-strict modes, a raised `ExpressionError` for `STRICT_RAISE`."""
        if v is _MISSING:
            if self._mode is ResolveMode.STRICT_RAISE:
                raise ExpressionError("unresolved reference in expression")
            return None
        return v

    # --- dispatch --- #
    def _walk(self, node: "Tree | Token") -> Any:
        if isinstance(node, Token):
            return self._token(node)
        handler = getattr(self, f"_do_{node.data}", None)
        if handler is None:
            raise ExpressionError(f"unsupported expression node {node.data!r}")
        return handler(node)

    def _token(self, tok: Token) -> Any:
        """A lone atom that inlined up to a top-level token: `${...}`, or a literal."""
        if tok.type == "WRAPPED_REF":
            return self._reference(str(tok)[2:-1])
        if tok.type == "NUMBER":
            s = str(tok)
            return float(s) if "." in s else int(s)
        if tok.type == "STRING":
            return str(tok)[1:-1]
        if tok.type == "BOOL":
            return str(tok).lower() == "true"
        if tok.type == "NULL":
            return None
        raise ExpressionError(f"unexpected token {tok.type} {tok!r}")

    # --- references / calls (`refcall`) --- #
    def _do_refcall(self, node: Tree) -> Any:
        """A `refcall` is a bare NAME + `trailer`s (a reference) OR a NAME +
        `call_suffix` + trailing `trailer`s (a builtin call, dotted access on its
        result). See `grammar` — the leading child is the NAME; trailers before any
        `call_suffix` are already rejected by `parse_expr`."""
        children = node.children
        name = str(children[0])
        call_suffix = next(
            (c for c in children if isinstance(c, Tree) and c.data == "call_suffix"), None
        )
        if call_suffix is None:
            segments = [str(c.children[0]) for c in children[1:]]  # each trailer's NAME
            return self._reference(".".join([name, *segments]))
        # a builtin call: dispatch through TEMPLATE_FNS, then dotted access on the result.
        value = self._call_builtin(name, call_suffix)
        after = [c for c in children if isinstance(c, Tree) and c.data == "trailer"]
        for tr in after:
            value = self._dotted_step(value, str(tr.children[0]))
        return value

    def _reference(self, path: str) -> Any:
        """Resolve a dotted reference path. An `item`-headed path walks the MAP-body
        `item` scope locally (dict-only — mirrors `template._resolve_path`'s item branch);
        every other path is handed WHOLE to `resolve` (the pool-agnostic seam — the
        resolver owns the dotted walk, exactly as `resolve_reference` / `_resolve_in_record`
        do today). A miss (`item` miss, or `resolve` -> None) yields `_MISSING`."""
        parts = path.split(".")
        if parts[0] == "item":  # MAP-body-local scope (not a resolver head)
            if self._item is None:
                return _MISSING
            value: Any = self._item
            for step in parts[1:]:
                value = self._dotted_step(value, step)
            return _MISSING if value is None else value
        resolved = self._resolve(path)
        return _MISSING if resolved is None else resolved

    def _dotted_step(self, value: Any, step: str) -> Any:
        """One dotted step — dict `.get` ONLY, never `getattr` (SAFETY-CRITICAL: so
        `${x.__class__}` can never reach a Python attribute). A non-dict / missing key
        -> None."""
        return value.get(step) if isinstance(value, dict) else None

    def _call_builtin(self, callee: str, call_suffix: Tree) -> Any:
        """Dispatch a builtin call through `TEMPLATE_FNS`. Positional and keyword
        `arg`s are distinguished by shape (a kwarg carries a leading NAME token).
        A builtin failure is wrapped as `ExpressionError`."""
        from agent_composer.expr.builtins import TEMPLATE_FNS

        fn = TEMPLATE_FNS.get(callee)
        if fn is None:
            raise ExpressionError(f"unknown expression function {callee!r}")
        pos: list = []
        kw: dict = {}
        for arg in call_suffix.children:  # each child is an `arg` subtree
            kids = arg.children
            if len(kids) == 2:  # kwarg: leading NAME token, then the value expr
                kw[str(kids[0])] = self._as_value(self._walk(kids[1]))
            else:  # positional: a single value expr
                pos.append(self._as_value(self._walk(kids[0])))
        try:
            return fn(*pos, **kw)
        except ExpressionError:
            raise
        except Exception as exc:  # a builtin blew up (bad arity/type) — surface loudly
            raise ExpressionError(f"expression function {callee!r} failed: {exc}") from exc

    # --- coalesce / default / required (short-circuit) --- #
    def _do_coalesce(self, node: Tree) -> Any:
        """`a | b | c` — first operand that resolves to a non-None value wins; a
        present falsy value (0 / "" / False) wins, only `None`/miss falls through."""
        for child in node.children:
            v = self._as_value(self._walk(child))
            if v is not None:
                return v
        return None

    def _do_default_expr(self, node: Tree) -> Any:
        """`head :- fallback` — the fallback is evaluated ONLY when `head` misses or
        resolves to None."""
        head, fallback = node.children
        v = self._as_value(self._walk(head))
        if v is not None:
            return v
        return self._as_value(self._walk(fallback))

    def _do_required_expr(self, node: Tree) -> Any:
        """`head :? message` — raise `RequiredError` (with `message`) when `head`
        misses / is None."""
        head, message = node.children
        v = self._as_value(self._walk(head))
        if v is None:
            from agent_composer.expr.template import RequiredError

            raise RequiredError(self._as_value(self._walk(message)))
        return v

    # --- boolean combinators --- #
    def _do_negate(self, node: Tree) -> Any:
        operand = [c for c in node.children if not isinstance(c, Token)]  # drop the NOT token
        return not self._as_value(self._walk(operand[-1]))

    def _do_or_expr(self, node: Tree) -> Any:
        # Operands fold by PYTHON truthiness, not bool-only (so `0 or "hit"` is truthy):
        # a deliberate change from the legacy `when:` bool-filter, matching the design
        # "operators delegate to Python semantics on resolved values".
        return any(self._as_value(self._walk(c)) for c in node.children if not isinstance(c, Token))

    def _do_and_expr(self, node: Tree) -> Any:
        # Operands fold by Python truthiness, not bool-only (see `_do_or_expr`): a
        # deliberate change from the legacy `when:` bool-filter.
        return all(self._as_value(self._walk(c)) for c in node.children if not isinstance(c, Token))

    # --- comparisons (reuse the locked `_eval_comparison` None-> False contract) --- #
    def _do_compare(self, node: Tree) -> Any:
        lhs, op, rhs = node.children
        return _eval_comparison(str(op), self._as_value(self._walk(lhs)), self._as_value(self._walk(rhs)))

    def _do_compare_in(self, node: Tree) -> Any:
        lhs, _op, rhs = node.children
        return _eval_comparison("in", self._as_value(self._walk(lhs)), self._as_value(self._walk(rhs)))

    def _do_compare_notin(self, node: Tree) -> Any:
        lhs, _op, rhs = node.children
        return _eval_comparison("not in", self._as_value(self._walk(lhs)), self._as_value(self._walk(rhs)))

    # --- arithmetic — Python operators on VALUES (NOT the number-only `_arith`) --- #
    def _binop(self, node: Tree, op: Callable[[Any, Any], Any], symbol: str) -> Any:
        lhs = self._as_value(self._walk(node.children[0]))
        rhs = self._as_value(self._walk(node.children[1]))
        try:
            return op(lhs, rhs)
        except ZeroDivisionError as exc:
            raise ExpressionError(f"division by zero: {lhs!r} {symbol} {rhs!r}") from exc
        except TypeError as exc:
            raise ExpressionError(f"type error evaluating {lhs!r} {symbol} {rhs!r}: {exc}") from exc

    def _do_add(self, node: Tree) -> Any:
        return self._binop(node, _value_add, "+")

    def _do_sub(self, node: Tree) -> Any:
        return self._binop(node, lambda a, b: a - b, "-")

    def _do_mul(self, node: Tree) -> Any:
        return self._binop(node, lambda a, b: a * b, "*")

    def _do_div(self, node: Tree) -> Any:
        return self._binop(node, lambda a, b: a / b, "/")

    def _do_mod(self, node: Tree) -> Any:
        return self._binop(node, lambda a, b: a % b, "%")

    def _do_power(self, node: Tree) -> Any:
        return self._binop(node, lambda a, b: a ** b, "**")

    def _do_neg(self, node: Tree) -> Any:
        v = self._as_value(self._walk(node.children[0]))
        try:
            return -v
        except TypeError as exc:
            raise ExpressionError(f"unary minus on non-number {v!r}: {exc}") from exc

    def _do_list_lit(self, node: Tree) -> Any:
        return [self._as_value(self._walk(c)) for c in node.children]


def eval_expr(
    tree: "Tree | Token",
    resolve: Callable[[str], Any],
    item: Any = None,
    mode: ResolveMode = ResolveMode.BINDING_NONE,
) -> Any:
    """
    Evaluate a unified `${...}` expression tree (from `expr.grammar.parse_expr`).

    The ONE evaluator for every `${...}` construct: references (bare / dotted /
    `${}`-wrapped / `item`-scoped), arithmetic and comparisons over VALUES (so
    `str+str` concatenates and `list+list` extends), boolean combinators, list
    literals, coalesce / default / required, and pure builtin calls.

    Three semantics are LOCKED (they are load-bearing for `case default` routing):
    a missing reference is `None` (in the non-strict modes); arithmetic over that
    `None` raises a loud `ExpressionError` (a wrapped `TypeError`); an ordered
    comparison with a `None` operand is `False` (missing->falsy).

    Dotted access is dict-key lookup ONLY — never `getattr` — so `${x.__class__}`
    can never reach a Python attribute (SAFETY-CRITICAL). Builtin calls dispatch
    through `expr.builtins.TEMPLATE_FNS` only (no arbitrary callables).

    Args:
        tree (`Tree | Token`):
            The parsed expression. A lone atom (`${a}`, a bare literal) inlines to a
            top-level `Token`; every other construct is a `Tree` — both are handled.
        resolve (`Callable[[str], Any]`):
            Resolves a reference path to its value; a path it maps to `None` is a MISS
            (the pool-agnostic seam). `item`-headed paths bypass this (see `item`).
        item (`Any`, *optional*, defaults to `None`):
            The MAP-body-local scope for `item` / `item.path` (dict-only walk). `None`
            means no item scope.
        mode (`ResolveMode`, *optional*, defaults to `BINDING_NONE`):
            How a missing reference is treated — raise (`STRICT_RAISE`) or become
            `None` (`BINDING_NONE` / `CONDITION_FALSY`).

    Returns:
        `Any`:
            The evaluated value (typed): a number / string / bool / list / dict, or
            `None`.

    Raises:
        `ExpressionError`:
            On an unsupported node, an unknown builtin, a builtin failure, arithmetic
            over an incompatible operand (incl. `None`), or a strict-mode miss.
        `RequiredError`:
            When a `head :? message` required atom's head misses / is None.
    """
    return _EvalExpr(resolve, item, mode).eval(tree)
