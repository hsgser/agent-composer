"""Tests for the ONE unified expression evaluator (`expr.expressions.eval_expr`).

These drive `eval_expr(tree, resolve, item, mode)` — the single evaluator that
walks a tree from `expr.grammar.parse_expr` against a `resolve` callable. Unlike
the parse-only grammar tests (`test_expr_grammar.py`), these assert computed
VALUES, the three missing-ref-by-mode behaviors, and the load-bearing semantics
(missing->None, arith-over-None->loud, cmp-with-None->False) that `case default`
routing depends on.
"""

import pytest

from agent_composer.expr.expressions import (
    ExpressionError,
    ResolveMode,
    eval_expr,
)
from agent_composer.expr.grammar import parse_expr
from agent_composer.expr.template import RequiredError


def _eval(text, resolve=None, item=None, mode=ResolveMode.BINDING_NONE):
    """Parse `text` then evaluate it, defaulting to an empty resolver (all miss)."""
    resolve = resolve or (lambda _path: None)
    return eval_expr(parse_expr(text), resolve, item=item, mode=mode)


def _from(mapping):
    """A `resolve` callable backed by a nested dict, with the dict-ONLY dotted walk
    the production resolvers do (mirrors `_resolve_in_record`): head lookup then
    `.get` per step, never `getattr`. A miss / non-dict step -> None."""

    def _resolve(path):
        parts = path.split(".")
        value = mapping.get(parts[0])
        for step in parts[1:]:
            value = value.get(step) if isinstance(value, dict) else None
        return value

    return _resolve


# --------------------------------------------------------------------------- #
# Operators on values (NOT the number-only legacy `_arith` gate)
# --------------------------------------------------------------------------- #


def test_int_addition():
    assert _eval("1 + 2") == 3


def test_string_concatenation():
    # str + str concatenates — the number-only gate must be bypassed.
    assert _eval('"a" + "b"') == "ab"


def test_list_concatenation():
    # list + list extends.
    assert _eval("x + y", _from({"x": [1], "y": [2]})) == [1, 2]


def test_power():
    assert _eval("2 ** 3") == 8


def test_subtraction_and_product_and_div():
    assert _eval("10 - 3") == 7
    assert _eval("4 * 5") == 20
    assert _eval("8 / 2") == 4
    assert _eval("7 % 3") == 1


def test_unary_minus():
    assert _eval("-x", _from({"x": 5})) == -5


# --------------------------------------------------------------------------- #
# Dotted access — dict-only, NEVER getattr (SAFETY-CRITICAL)
# --------------------------------------------------------------------------- #


def test_dotted_access_dict_key():
    assert _eval("x.k", _from({"x": {"k": 42}})) == 42


def test_dotted_access_never_getattr():
    # `${obj.__class__}` must MISS (dict has no key "__class__") — never reach the
    # Python attribute. Proves dotted access is dict-key lookup only.
    assert _eval("${obj.__class__}", _from({"obj": {"a": 1}})) is None


# --------------------------------------------------------------------------- #
# Missing-ref by mode — the three locked behaviors
# --------------------------------------------------------------------------- #


def test_binding_none_missing_is_none():
    # LOCKED: a missing ref resolves to None in the non-strict binding mode.
    assert _eval("a", mode=ResolveMode.BINDING_NONE) is None


def test_condition_falsy_missing_ordered_compare_is_false():
    # LOCKED: an ordered comparison with a missing (None) operand -> False.
    assert _eval("missing > 5", mode=ResolveMode.CONDITION_FALSY) is False


def test_condition_falsy_arith_over_none_is_loud():
    # LOCKED: arithmetic over a missing (None) operand propagates a loud TypeError,
    # wrapped as ExpressionError (NOT silently None/0).
    with pytest.raises(ExpressionError):
        _eval("missing + 1 > 5", mode=ResolveMode.CONDITION_FALSY)


def test_strict_raise_missing_raises():
    with pytest.raises(ExpressionError):
        _eval("a", mode=ResolveMode.STRICT_RAISE)


def test_binding_none_wrapped_ref_missing_is_none():
    assert _eval("${a}", mode=ResolveMode.BINDING_NONE) is None


# --------------------------------------------------------------------------- #
# Coalesce / default / required
# --------------------------------------------------------------------------- #


def test_coalesce_first_non_none():
    assert _eval("a | b | c", _from({"b": "second", "c": "third"})) == "second"


def test_coalesce_present_falsy_wins():
    # A present falsy value (0 / "" / False) is NOT None, so it wins the coalesce.
    assert _eval("a | b", _from({"a": 0})) == 0


def test_coalesce_short_circuits():
    # fallback (1/0) must NOT be evaluated when the head is present.
    assert _eval("a | 1 / 0", _from({"a": "present"})) == "present"


def test_default_when_missing():
    assert _eval('a :- "d"') == "d"


def test_default_short_circuits_fallback():
    # fallback (1/0) must NOT be evaluated when the head is present — a vacuous
    # inert literal fallback would pass even if short-circuit broke.
    assert _eval("a :- 1 / 0", _from({"a": "present"})) == "present"


def test_nested_default_wrapped_ref():
    # `a :- ${b}`: the default RHS is itself a ref, resolved when `a` is missing.
    assert _eval("a :- ${b}", _from({"b": "fallback"})) == "fallback"


def test_required_raises_when_missing():
    with pytest.raises(RequiredError):
        _eval('a :? "m"')


def test_required_short_circuits_fallback():
    # message (1/0) must NOT be evaluated when the head is present.
    assert _eval("a :? 1 / 0", _from({"a": "here"})) == "here"


# --------------------------------------------------------------------------- #
# Pure builtin dispatch through TEMPLATE_FNS
# --------------------------------------------------------------------------- #


def test_builtin_upper():
    assert _eval("upper(name)", _from({"name": "hi"})) == "HI"


def test_builtin_join():
    assert _eval('join(items, ", ")', _from({"items": ["a", "b"]})) == "a, b"


def test_builtin_kwarg():
    assert _eval("upper(s=name)", _from({"name": "hi"})) == "HI"


def test_builtin_call_result_dotted_access(monkeypatch):
    # dotted access on a call result: the builtin returns a dict, `.field` reads it.
    from agent_composer.expr import builtins as _bi

    monkeypatch.setitem(_bi.TEMPLATE_FNS, "identity", lambda v: v)
    assert _eval("identity(d).k", _from({"d": {"k": "v"}})) == "v"


def test_unknown_builtin_raises():
    with pytest.raises(ExpressionError):
        _eval("no_such_fn(x)", _from({"x": 1}))


# --------------------------------------------------------------------------- #
# `item` scope (MAP-body-local)
# --------------------------------------------------------------------------- #


def test_item_scope():
    assert _eval("item.title", item={"title": "T"}) == "T"


def test_item_head_bare():
    assert _eval("item", item={"a": 1}) == {"a": 1}


def test_item_scope_dict_only():
    # dotted access under `item` is also dict-only, never getattr.
    assert _eval("item.__class__", item={"a": 1}) is None


# --------------------------------------------------------------------------- #
# `and` / `or` fold by PYTHON truthiness (NOT the legacy bool-only filter)
# --------------------------------------------------------------------------- #


def test_or_folds_by_python_truthiness():
    # `x or y` with x a FALSY non-bool (0) and y truthy: the non-bool operand
    # participates (legacy `when:` filtered to bools only). Result is True since y
    # is truthy — pins the deliberate Python-truthiness semantics.
    assert _eval("x or y", _from({"x": 0, "y": "hit"})) is True


def test_and_folds_by_python_truthiness():
    # `x and y` with x a falsy non-bool (""): the empty string participates as
    # falsy, so the `and` is False (not filtered out as a non-bool).
    assert _eval("x and y", _from({"x": "", "y": "hit"})) is False


# --------------------------------------------------------------------------- #
# Cheap edge cases later steps rely on
# --------------------------------------------------------------------------- #


def test_not_in_list_true():
    assert _eval('"z" not in items', _from({"items": ["a", "b"]})) is True


def test_not_in_list_false():
    assert _eval('"a" not in items', _from({"items": ["a", "b"]})) is False


def test_unary_minus_on_missing_ref_raises_loudly():
    # `-missing` in binding-none: the miss coerces to None, and unary minus over
    # None raises a loud (wrapped) ExpressionError — NOT a silent value.
    # (loud-arith-adjacent: PINNED to the observed behavior.)
    with pytest.raises(ExpressionError):
        _eval("-missing", mode=ResolveMode.BINDING_NONE)


def test_list_lit_with_missing_element():
    # `[a, 1]` with `a` missing (binding-none): the miss becomes None inside the list.
    assert _eval("[a, 1]", mode=ResolveMode.BINDING_NONE) == [None, 1]
