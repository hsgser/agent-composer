r"""One unified `${...}` expression grammar — PARSE ONLY (no evaluation here).

This is the single grammar that supersedes the three divergent `${...}` dialects
the engine grew (binding coalesce/default, `when:` boolean/arithmetic, prompt
builtin-call). It parses every construct of all three into one Lark tree; the
evaluator that walks that tree is a LATER step and deliberately lives elsewhere.

## What it parses

- References: bare `a`, dotted `a.b.c`, and — for the runtime graph-expansion
  separators — segments containing `#` / `/` (`node#0.output`, `def/child.output`).
- The `${...}`-wrapped back-compat ref form (`${a}`), so old sources keep parsing.
- Arithmetic (`+ - * / %` and `**`), unary minus, comparisons
  (`== != < <= > >=`), `and` / `or` / `not`, `in` / `not in`.
- List literals (`[1, "x"]`) and parenthesised sub-expressions.
- Pure builtin calls (`upper(name)`, `join(items, ", ")`), with dotted access on
  the result (`fn(x).field`) — mirroring the prompt call form.
- Coalesce (`a | b | c`), default (`a :- d`), required (`a :? "msg"`), including a
  wrapped-ref default RHS (`a :- ${b}`).

## The terminal design — the load-bearing "C1" fix

A naive grammar with a SEPARATE bare-ref terminal AND a separate
`call: NAME "(" ... ")"` rule does NOT parse under LALR: the lexer matches the
bare-ref terminal on `upper` before it can ever see the `(`, so the call rule is
unreachable and `upper(name)` fails. The fix — verified — is ONE shared `NAME`
terminal with an OPTIONAL call-suffix: a `refcall` is a `NAME`, then dotted
`trailer`s, then an OPTIONAL `call_suffix`. No suffix => a reference (path = the
NAME joined with its trailer segments); a suffix present => a builtin call (callee
= the leading NAME, any trailers AFTER the parens are dotted access on the
result). A `call_suffix` WITH a leading trailer (`a.b(...)`) is rejected in
`parse_expr` — builtins are bare-callee only, as the prompt form is today.

## Charset decision — why `-` is NOT in `NAME` (and `#` / `/` are)

`NAME` is `/[A-Za-z_][A-Za-z0-9_#\/]*/`. `-` is deliberately EXCLUDED: were it in
the terminal, `a - b` would lex as the single identifier `a-b` and subtraction
would break. Consequence: references inside `${}` use this non-hyphen charset, so
a hyphenated identifier is unavailable INSIDE an expression. That only matters for
the later `call(...)` directive, whose flow-id may be hyphenated — but that is a
loader-side string match (a directive recognizer), NOT this grammar, so it is
unaffected. `#` and `/` ARE allowed because runtime graph expansion mints segments
like `node#0` and `def/child`.

## Keyword priority

The keyword terminals (`and` `or` `not` `in` `not in` `true` `false` `null`
`none`) carry a lexer priority higher than `NAME` (as in the current
`expressions.py`), so `and` / `in` win the tie and do not lex as ordinary refs.
"""

from lark import Lark, Token, Tree
from lark.exceptions import LarkError

# Reuse the existing expression error — `expressions.ExpressionError` is already
# the base of `expressions.RequiredError`, so parse failures here stay in one family.
from agent_composer.expr.expressions import ExpressionError

# The unified grammar. Precedence, lowest to highest:
#   coalesce (`|`) -> default/required (`:-`/`:?`) -> or -> and -> not
#   -> comparison (incl. in / not in) -> sum -> product -> unary minus
#   -> power (`**`, right-assoc) -> atom.
# `?rule` inlines a single-child parse to keep the tree flat (Lark convention).
_GRAMMAR = r"""
?start: coalesce

// coalesce (`|`) — lowest precedence. `a :- b | c` => `(a:-b) | c`. The two
// alternatives keep the `?` inline effective: a lone operand stays its own tree
// (no wrapper), and a `coalesce` node appears ONLY when a `|` is actually present.
?coalesce: default_expr
         | default_expr ("|" default_expr)+   -> coalesce

// default / required bind tighter than `|`, looser than boolean/arithmetic.
?default_expr: or_expr
             | or_expr ":-" or_expr   -> default_expr
             | or_expr ":?" or_expr   -> required_expr

?or_expr: and_expr (OR and_expr)*
?and_expr: not_expr (AND not_expr)*
?not_expr: NOT not_expr   -> negate
         | comparison
?comparison: sum
           | sum COMP_OP sum   -> compare
           | sum IN sum        -> compare_in
           | sum NOT_IN sum    -> compare_notin

?sum: product
    | sum "+" product   -> add
    | sum "-" product   -> sub
?product: unary
        | product "*" unary   -> mul
        | product "/" unary   -> div
        | product "%" unary   -> mod
// unary minus binds looser than `**` (Python: `-x ** 2` == `-(x ** 2)`).
?unary: power
      | "-" unary   -> neg
// `**` is right-associative and binds tighter than unary minus.
?power: atom
      | atom "**" unary   -> power

?atom: refcall
     | NUMBER | STRING | BOOL | NULL
     | list_lit
     | WRAPPED_REF
     | "(" coalesce ")"

// ONE shared NAME terminal + optional call-suffix (the C1 fix):
//   no call_suffix  => a reference (NAME + trailer segments)
//   a call_suffix   => a builtin call (leading NAME is the callee; trailers
//                      AFTER the parens are dotted access on the result). A
//                      trailer BEFORE the parens (`a.b(...)`) is a dotted callee
//                      and is rejected in `parse_expr`.
refcall: NAME trailer* (call_suffix trailer*)?
trailer: "." NAME
call_suffix: "(" [arg ("," arg)*] ")"
arg: [NAME "="] coalesce

// list literal: elements are full expressions (refs / literals / arithmetic).
list_lit: "[" [coalesce ("," coalesce)*] "]"

COMP_OP: "==" | "!=" | "<=" | ">=" | "<" | ">"
WRAPPED_REF: /\$\{[^}]+\}/
STRING: /"[^"]*"/ | /'[^']*'/
NUMBER: /\d+(\.\d+)?/

// `#` and `/` ARE in NAME (graph-expansion segments); `-` is NOT (else `a - b`
// lexes as one ident and subtraction breaks — see the module docstring).
NAME: /[A-Za-z_][A-Za-z0-9_#\/]*/

// Keyword terminals outrank NAME so `and`/`in`/... win the lexer tie.
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

# The shared LALR parser instance (built once at import). `maybe_placeholders`
# False so optional branches drop rather than inserting `None` placeholders into
# the tree.
_PARSER = Lark(_GRAMMAR, parser="lalr", maybe_placeholders=False)


def _reject_dotted_callee(tree: Tree | Token) -> None:
    """Raise if any `refcall` is a call (`call_suffix`) with a leading `trailer`.

    Builtins are bare-callee only: `a.b(x)` — a dotted head applied like a call —
    is not a valid builtin call and must be rejected, matching today's prompt
    form. A call whose ONLY trailers follow the parens (`fn(x).field`, dotted
    access on the result) is fine; those trailers are parsed after `call_suffix`,
    so the guard checks for a trailer appearing BEFORE the suffix in the child
    order.

    A lone atom (e.g. `${a}`, a bare `NUMBER`/`STRING`) inlines all the way up to
    a top-level `Token` now that no spurious `coalesce` wraps it — such a top has
    no subtrees to check, so it is trivially accepted.
    """
    if not isinstance(tree, Tree):
        return
    for node in tree.iter_subtrees():
        if node.data != "refcall":
            continue
        has_call = False
        trailer_before_call = False
        for child in node.children:
            if isinstance(child, Tree) and child.data == "call_suffix":
                has_call = True
            elif isinstance(child, Tree) and child.data == "trailer" and not has_call:
                trailer_before_call = True
        if has_call and trailer_before_call:
            raise ExpressionError(
                "builtin call must have a bare callee (no dotted access before "
                "'('): a form like 'a.b(x)' is not allowed"
            )


def parse_expr(text: str) -> Tree | Token:
    """
    Parse one unified `${...}` expression into a Lark tree (PARSE ONLY).

    This does no evaluation and touches no variable pool — it is the shared front
    end for the later evaluator. A dotted-callee builtin call (`a.b(x)`) is
    rejected here; every other construct in the module docstring parses.

    Args:
        text (`str`):
            The expression source (the interior of a `${...}`, or a bare
            expression). May be any construct the grammar accepts.

    Returns:
        `Tree | Token`:
            The Lark parse tree. Its shape is the grammar above: `refcall` for a
            ref-or-call, `call_suffix` marking a call, `coalesce` (present ONLY
            when a `|` actually appears) / `default_expr` / `required_expr` at the
            top, and the arithmetic/boolean chain below. A LONE atom — a bare
            `${a}` wrapped-ref, or a bare `NUMBER` / `STRING` — inlines all the
            way up and comes back as a top-level `Token` (no wrapper node), since
            no spurious one-child `coalesce` is inserted.

    Raises:
        `ExpressionError`:
            If `text` does not parse, or is a dotted-callee builtin call.
    """
    try:
        tree = _PARSER.parse(text)
    except LarkError as exc:
        raise ExpressionError(f"could not parse expression {text!r}: {exc}") from exc
    _reject_dotted_callee(tree)
    return tree
