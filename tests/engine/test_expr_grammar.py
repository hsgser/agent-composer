"""Parse-only tests for the unified `${...}` expression grammar (`expr.grammar`).

These assert PARSE SUCCESS and coarse TREE SHAPE only — evaluation is Step 2, so
no value is computed here. The heart of the file is the five "C1" regression
asserts: the exact LALR-collision cases that a naive two-terminal grammar (a bare
`REF` terminal plus a separate `call: NAME "(" ... ")"` rule) fails to parse,
because the lexer matches the bare-ref terminal before it can see the `(`. The
fix under test is ONE shared `NAME` terminal with an optional call-suffix.
"""

import pytest
from lark import Tree

from agent_composer.expr.expressions import ExpressionError
from agent_composer.expr.grammar import parse_expr


def _tree(text: str) -> Tree:
    """Parse `text` and assert we got a Lark `Tree` back (a parse, not an error)."""
    result = parse_expr(text)
    assert isinstance(result, Tree), f"{text!r} did not parse to a Tree"
    return result


def _kinds(tree: Tree) -> set:
    """The set of rule/data names anywhere in `tree` — the coarse shape probe."""
    names = set()
    for node in tree.iter_subtrees():
        names.add(node.data)
    return names


# --------------------------------------------------------------------------- #
# References
# --------------------------------------------------------------------------- #


def test_bare_ref():
    assert "refcall" in _kinds(_tree("a"))


def test_dotted_ref():
    tree = _tree("a.b.c")
    assert "refcall" in _kinds(tree)
    # a.b.c is ONE dotted ref: one refcall with trailers, not nested calls.
    refcalls = [n for n in tree.iter_subtrees() if n.data == "refcall"]
    assert len(refcalls) == 1


def test_ref_with_hash():
    assert "refcall" in _kinds(_tree("node#0.output"))


def test_ref_with_slash():
    assert "refcall" in _kinds(_tree("def/child.output"))


def test_wrapped_ref():
    # `${a}` back-compat form must still parse.
    _tree("${a}")


def test_item_ref():
    assert "refcall" in _kinds(_tree("item.title"))


# --------------------------------------------------------------------------- #
# Arithmetic / comparisons / booleans
# --------------------------------------------------------------------------- #


def test_arithmetic():
    _tree("1 + 2 * 3 - 4 / 5 % 6")


def test_power():
    assert "power" in _kinds(_tree("2 ** 3"))


def test_unary_power_precedence():
    # `-x ** 2` must parse (evaluator-side it is `-(x ** 2)`, per Python).
    _tree("-x ** 2")


def test_comparisons():
    for op in ("==", "!=", "<", "<=", ">", ">="):
        _tree(f"a {op} b")


def test_and_or_not():
    _tree("a and b or not c")


def test_in_and_not_in():
    _tree("a in b")
    _tree("a not in b")


def test_list_literal():
    _tree('[1, "x"]')


def test_parens():
    _tree("(a + b) * c")


def test_unary_minus():
    _tree("-x")


def test_subtract_of_negative():
    # `3 - -x`: subtraction of a unary-negated ref.
    _tree("3 - -x")


# --------------------------------------------------------------------------- #
# Builtin calls
# --------------------------------------------------------------------------- #


def test_pure_builtin_call():
    tree = _tree("upper(name)")
    assert "call_suffix" in _kinds(tree)


def test_builtin_call_with_string_arg():
    tree = _tree('join(items, ", ")')
    assert "call_suffix" in _kinds(tree)


def test_call_result_dotted_access():
    # dotted access on a call result (mirrors the prompt `_PromptCall.trailing`).
    _tree("fn(x).field")


def test_dotted_callee_rejected():
    # builtins are bare-callee only: `a.b(...)` must be rejected.
    with pytest.raises(ExpressionError):
        parse_expr("a.b(x)")


# --------------------------------------------------------------------------- #
# Coalesce / default / required
# --------------------------------------------------------------------------- #


def test_coalesce():
    tree = _tree("a | b | c")
    assert "coalesce" in _kinds(tree)


def test_default():
    tree = _tree('a :- "d"')
    assert "default_expr" in _kinds(tree)


def test_required():
    tree = _tree('a :? "msg"')
    assert "required_expr" in _kinds(tree)


def test_nested_default():
    # `a :- ${b}`: a wrapped-ref default RHS.
    _tree("a :- ${b}")


def test_default_binds_tighter_than_coalesce():
    # `a :- b | c` parses as `(a :- b) | c`: the coalesce is the top node with
    # two operands, the first being the default.
    tree = _tree("a :- b | c")
    coalesce = next(n for n in tree.iter_subtrees() if n.data == "coalesce")
    assert len(coalesce.children) == 2
    assert isinstance(coalesce.children[0], Tree)
    assert coalesce.children[0].data == "default_expr"


def test_coalesce_below_arithmetic():
    # `a | b + 1` parses as `a | (b + 1)`: the coalesce's second operand is a sum.
    tree = _tree("a | b + 1")
    coalesce = next(n for n in tree.iter_subtrees() if n.data == "coalesce")
    assert len(coalesce.children) == 2
    kinds_rhs = _kinds(coalesce.children[1]) if isinstance(coalesce.children[1], Tree) else set()
    assert "add" in kinds_rhs


# --------------------------------------------------------------------------- #
# C1 regression asserts — the exact LALR-collision cases that MUST pass.
# --------------------------------------------------------------------------- #


def test_c1_upper_name_is_a_call():
    """`upper(name)` -> a call (callee `upper`, one ref arg), NOT bare-ref + junk."""
    tree = _tree("upper(name)")
    assert "call_suffix" in _kinds(tree)


def test_c1_join_with_string_literal():
    """`join(items, ", ")` -> callee + ref arg + string-literal arg."""
    tree = _tree('join(items, ", ")')
    assert "call_suffix" in _kinds(tree)
    assert "arg" in _kinds(tree)


def test_c1_dotted_ref_single():
    """`a.b.c` -> a single dotted ref (one refcall)."""
    tree = _tree("a.b.c")
    refcalls = [n for n in tree.iter_subtrees() if n.data == "refcall"]
    assert len(refcalls) == 1


def test_c1_hash_in_segment():
    """`node#0.output` -> parses (`#` in a segment)."""
    _tree("node#0.output")


def test_c1_subtraction_not_one_ident():
    """`a - b` -> subtraction of two refs, NOT one identifier `a-b`.

    The trap: if `-` is in the shared NAME terminal, `a - b` lexes as a single
    ident and this fails. Two refcalls under a `sub` node proves the split.
    """
    tree = _tree("a - b")
    assert "sub" in _kinds(tree)
    refcalls = [n for n in tree.iter_subtrees() if n.data == "refcall"]
    assert len(refcalls) == 2
