"""The `${...}` template language — binding values AND strict prompt rendering.

A binding value (a node input `from:`, a flow `outputs:` entry) is a TEMPLATE:
plain text interspersed with `${...}` spans, with `$$` for a literal `$`.

- A value that is EXACTLY one `${...}` resolves to the **typed** value of that
  reference (a float stays a float, a list a list, an object a dict).
- A `${...}` **embedded** in surrounding text is **stringified** into it.
- A value with no `${...}` is a plain literal (after `$$` -> `$`).

The interior of a `${...}` is a COALESCE of atoms, `|`-separated, first-non-None
(`null` ≡ no value):
- `outputs.x.y`        — a reference
- `x:-default`         — value, else a literal default OR ONE nested `${...}`
- `x:?message`         — required (raise if unbound)
- a literal            — number / bool / null / quoted string

Nesting is ONE level: `${x:-${y}}` is allowed; `${x:-${y:-${z}}}` is an error
(use `|` for multi-way chains: `${x | y | z}`).

Pure parse/eval (no pool): `eval_binding` takes a `resolve` callable so this stays
a leaf both `nodes` (runtime bind) and `compile` (the compile-time reference walk)
may import.

This module also owns the strict AGENT/HUMAN_INPUT prompt renderer
(`render_template_record`) and its compile-time companion (`prompt_refs`): a prompt is
NOT a binding (it reads already-bound declared inputs, mints no edge), but it is a
`${...}` template, and it reuses these scanners — so it lives here rather than in
`expressions` (which cannot import this module without a cycle).

Knows about: `expr.expressions` (peer — `_parse_literal`, `ExpressionError`) and
`expr.builtins` (the prompt `TEMPLATE_FNS` registry).
Never imports: `nodes`, `compile`, `runtime`, `state` (pool-agnostic via `resolve`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from agent_composer.expr import grammar
from agent_composer.expr.builtins import TEMPLATE_FNS
from agent_composer.expr.expressions import (
    ExpressionError,
    ResolveMode,
    _parse_literal,
    eval_expr,
    expr_refs,
)

if TYPE_CHECKING:  # `Tree`/`Token` appear ONLY in the `Span.tree` annotation
    from lark import Token, Tree

_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_#/]*(\.[A-Za-z_][A-Za-z0-9_#/]*)*$")


class RequiredError(ExpressionError):
    """A `${ref:?message}` whose ref was unbound (the binder maps it to BindingError)."""


# --------------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Lit:
    value: Any


@dataclass(frozen=True)
class _Ref:
    path: str


@dataclass(frozen=True)
class _Default:
    path: str
    default: Any  # a literal value, OR a _Coalesce (one nested ${...})


@dataclass(frozen=True)
class _Required:
    path: str
    message: str


_Atom = Union[_Lit, _Ref, _Default, _Required]


@dataclass(frozen=True)
class _Coalesce:
    atoms: tuple  # tuple[_Atom, ...]


@dataclass(frozen=True)
class _Text:
    text: str


_Segment = Union[_Text, _Coalesce]


# --------------------------------------------------------------------------- #
# Scanning helpers — quote- AND brace-aware
# --------------------------------------------------------------------------- #


def _split_top_level(s: str, sep: str) -> list:
    """Split `s` on the single char `sep`, ignoring `sep` inside '...'/\"...\" quotes
    and inside `${...}` spans (brace depth)."""
    out: list = []
    buf: list = []
    quote: Optional[str] = None
    depth = 0
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if quote is not None:
            buf.append(c)
            if c == quote:
                quote = None
        elif c in ("'", '"'):
            quote = c
            buf.append(c)
        elif c == "$" and s[i + 1 : i + 2] == "{":
            depth += 1
            buf.append("${")
            i += 2
            continue
        elif depth > 0:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            buf.append(c)
        elif c == sep:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    out.append("".join(buf))
    return out


def _find_op(s: str) -> tuple:
    """(op, index) of the FIRST top-level `:-`/`:?` outside quotes and `${...}` spans,
    else (None, -1)."""
    quote: Optional[str] = None
    depth = 0
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if quote is not None:
            if c == quote:
                quote = None
        elif c in ("'", '"'):
            quote = c
        elif c == "$" and s[i + 1 : i + 2] == "{":
            depth += 1
            i += 2
            continue
        elif depth > 0:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
        elif c == ":" and s[i + 1 : i + 2] in ("-", "?"):
            return (":" + s[i + 1], i)
        i += 1
    return (None, -1)


def _default_literal(token: str) -> Any:
    """A `:-` default RHS literal — a bare word is a literal string (no quotes)."""
    try:
        return _parse_literal(token)
    except ExpressionError:
        return token


def _check_path(path: str) -> None:
    if not _PATH_RE.match(path):
        raise ExpressionError(f"malformed reference path {path!r}")


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #


def parse_binding(s: str) -> list:
    """Tokenize a binding string into template segments (`_Text` | `_Coalesce`).
    Handles `$$` -> `$` and brace-balanced (quote-aware) `${...}` spans."""
    segs: list = []
    buf: list = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "$" and s[i + 1 : i + 2] == "$":
            buf.append("$")
            i += 2
        elif s[i] == "$" and s[i + 1 : i + 2] == "{":
            if buf:
                segs.append(_Text("".join(buf)))
                buf = []
            depth, j, quote = 1, i + 2, None
            while j < n and depth:
                ch = s[j]
                if quote is not None:
                    if ch == quote:
                        quote = None
                elif ch in ("'", '"'):
                    quote = ch
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                if depth:
                    j += 1
            if depth:
                raise ExpressionError(f"unbalanced '${{' in binding {s!r}")
            segs.append(parse_interior(s[i + 2 : j], nested_ok=True))
            i = j + 1
        else:
            buf.append(s[i])
            i += 1
    if buf:
        segs.append(_Text("".join(buf)))
    return segs


def parse_interior(interior: str, *, nested_ok: bool) -> _Coalesce:
    """Parse the inside of a `${...}` into a coalesce of atoms."""
    return _Coalesce(tuple(parse_atom(a.strip(), nested_ok=nested_ok) for a in _split_top_level(interior, "|")))


def parse_atom(s: str, *, nested_ok: bool) -> _Atom:
    if not s:
        raise ExpressionError("empty coalesce operand")
    op, idx = _find_op(s)
    if op == ":?":
        path, msg = s[:idx].strip(), s[idx + 2 :].strip()
        _check_path(path)
        return _Required(path, msg)
    if op == ":-":
        path, rhs = s[:idx].strip(), s[idx + 2 :].strip()
        _check_path(path)
        if rhs.startswith("${") and rhs.endswith("}"):  # a nested ${...} default — ONE level
            if not nested_ok:
                raise ExpressionError(
                    f"only one nested '${{...}}' default is allowed; use '|' for chains: {s!r}"
                )
            return _Default(path, parse_interior(rhs[2:-1], nested_ok=False))
        return _Default(path, _default_literal(rhs))
    # no op: a literal (number/bool/null/quoted), else a bare ref
    try:
        return _Lit(_parse_literal(s))
    except ExpressionError:
        _check_path(s)
        return _Ref(s)


# --------------------------------------------------------------------------- #
# Inline call desugar — `${ f(arg=...) }` -> a synth `outputs.<id>` ref
#
# An inline call is applicative expression syntax: `f x` written inside a binding
# instead of `let t = f x in … t`. `desugar_calls` is the pure string→string +
# plain-data half: it scans a binding's `${…}` spans, finds each call operand
# `<ident>(<args>)` (in coalesce-operand position), mints a fresh id via `next_id`,
# emits an `InlineCall`, and rewrites the operand to `outputs.<id>`. The compose
# layer (`compose.calls`) turns each `InlineCall` into a synth `call` node — so the
# runtime sees only ordinary `call` nodes (pure load-time sugar, no eval-time call).
#
# Keyword args only; each arg VALUE is a full binding — a `${…}`-bearing value (or a
# quoted scalar) stays a string (ref / coalesce / embedded / template), else it is a
# literal (so `window=30` binds int 30). The literal grammar is a deliberate YAML-1.1
# SUBSET — see `_arg_source`. A nested inline call in an arg value desugars inner-first
# (recursion). The existing
# `parse_binding`/`parse_atom` grammar is untouched: by the time it runs, no calls
# remain. This module knows nothing of the `__call_` id convention (it lives in
# `compose.calls`) — it only calls `next_id()`.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InlineCall:
    """One desugared inline call: the synth node `id`, the `callee` name, and the
    keyword `args` (name -> a YAML-scalar literal value or a `${…}` binding string)."""

    id: str
    callee: str
    args: dict  # name -> a literal value or a `${…}` binding string (see `_arg_source`)


def desugar_calls(s: str, next_id: Callable[[], str]) -> tuple:
    """Rewrite every inline call in binding string `s` to a `${<id>.output}` ref.

    Returns `(new_s, calls)`; `calls` lists the `InlineCall`s discovered, with each
    nested call preceding its enclosing call (inner-first). `next_id()` mints each
    synth id. A string with no inline call round-trips unchanged (modulo coalesce
    whitespace); an unbalanced `${`/paren is left verbatim for the downstream parser
    to locate. Raises `ExpressionError` on a malformed call (e.g. a positional arg)."""
    calls: list = []
    out: list = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "$" and s[i + 1 : i + 2] == "$":
            out.append("$$")  # a literal `$` escape — never a span start
            i += 2
        elif s[i] == "$" and s[i + 1 : i + 2] == "{":
            j = _find_span_end(s, i + 2)
            if j is None:  # unbalanced `${` — leave the rest for parse_binding to error
                out.append(s[i:])
                break
            interior, span_calls = _desugar_interior(s[i + 2 : j], next_id)
            calls.extend(span_calls)
            out.append("${" + interior + "}")
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out), calls


def _desugar_interior(interior: str, next_id: Callable[[], str]) -> tuple:
    """Desugar the inside of one `${…}`: split on top-level `|` (coalesce operands),
    desugar each operand, rejoin. Only an operand that IS a call is rewritten."""
    calls: list = []
    operands: list = []
    for op in _split_calls_aware(interior, "|"):
        new_op, op_calls = _desugar_operand(op, next_id)
        operands.append(new_op)
        calls.extend(op_calls)
    return "|".join(operands), calls


def _desugar_operand(op: str, next_id: Callable[[], str]) -> tuple:
    """If `op` is exactly `<ident>(<args>)`, desugar it to `outputs.<id>` (recursing
    into each arg value inner-first), emitting the nested calls then this call; else
    return `op` unchanged."""
    matched = _match_call(op)
    if matched is None:
        return op, []
    callee, args_str = matched
    calls: list = []
    args: dict = {}
    for pair in _split_calls_aware(args_str, ","):
        if not pair.strip():
            continue  # `f()` — no args
        name, value = _split_kv(pair)
        new_value, inner = desugar_calls(value, next_id)  # inner-first recursion
        calls.extend(inner)
        args[name] = _arg_source(new_value)
    cid = next_id()
    calls.append(InlineCall(id=cid, callee=callee, args=args))
    return cid + ".output", calls   # node-first ref shape


def _match_call(op: str) -> Optional[tuple]:
    """`op` (one coalesce operand) -> `(callee, args_str)` if it is exactly
    `<ident>(<balanced parens>)` (the callee ident allows `-` for flow ids), else
    None. Trailing content after the close paren (e.g. `.field`) -> None: not a clean
    inline call — left to the downstream grammar (inline calls have no dotted access)."""
    op2 = op.strip()
    m = re.match(r"([A-Za-z_][A-Za-z0-9_-]*)\s*\(", op2)
    if m is None:
        return None
    open_idx = m.end() - 1
    close_idx = _find_paren_end(op2, open_idx + 1)
    if close_idx is None:
        return None  # unbalanced parens — fall through to the downstream parser
    if op2[close_idx + 1 :].strip():
        return None  # trailing content (dotted access, etc.) — not a bare call
    return m.group(1), op2[open_idx + 1 : close_idx]


def _split_kv(pair: str) -> tuple:
    """Split one arg `name=value` on its FIRST top-level `=` (keyword args only)."""
    parts = _split_calls_aware(pair, "=")
    if len(parts) < 2:
        raise ExpressionError(
            f"inline call arg {pair.strip()!r} must be keyword (name=value)"
        )
    return parts[0].strip(), "=".join(parts[1:])


def _arg_source(value: str) -> Any:
    """One inline-call arg value -> its source, mirroring the named form's `inputs:`:

    - a fully-quoted scalar (`"…"`/`'…'`) -> its inner text (quotes stripped, like a
      YAML quoted scalar): a binding template if it interpolates (`"hi ${name}"` ->
      `hi ${name}`), else a plain string;
    - an unquoted `${…}`-bearing value -> the binding string (ref / coalesce / embedded);
    - else a bare token -> a literal via `_default_literal` (number / bool / null / a
      bare string), so `window=30` binds int 30.

    NB this literal grammar is a deliberate SUBSET of YAML 1.1: a bare `yes`/`no`/`on`/
    `off`/`None` stays a string (no boolean/null coercion) — avoiding the YAML bool
    footgun. Quote, number, true/false, and null spellings match the named form."""
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] in ("'", '"') and stripped[-1] == stripped[0]:
        return stripped[1:-1]  # a quoted scalar -> its inner text (template or literal)
    if "${" in stripped:
        return stripped
    return _default_literal(stripped)


def _find_span_end(s: str, start: int) -> Optional[int]:
    """Index of the `}` closing a `${…}` span whose interior begins at `start`
    (brace-depth + quote aware), or None if unbalanced. Mirrors `parse_binding`."""
    depth, quote, i, n = 1, None, start, len(s)
    while i < n:
        c = s[i]
        if quote is not None:
            if c == quote:
                quote = None
        elif c in ("'", '"'):
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _find_paren_end(s: str, start: int) -> Optional[int]:
    """Index of the `)` closing a call whose args begin at `start` — paren-depth
    aware, skipping `${…}` spans (their inner parens) and quoted text; None if
    unbalanced."""
    depth, quote, i, n = 1, None, start, len(s)
    while i < n:
        c = s[i]
        if quote is not None:
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
        elif c == "$" and s[i + 1 : i + 2] == "{":
            end = _find_span_end(s, i + 2)
            if end is None:
                return None
            i = end + 1
            continue
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _split_calls_aware(s: str, sep: str) -> list:
    """Split `s` on the single char `sep` at top level — ignoring `sep` inside quotes,
    `${…}` spans (brace depth), and parentheses (call args). The desugar's splitter:
    `_split_top_level` predates inline calls and is paren-blind, so calls (which add
    top-level bare parens) need this one."""
    out: list = []
    buf: list = []
    quote: Optional[str] = None
    brace = 0
    paren = 0
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if quote is not None:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
        elif c == "$" and s[i + 1 : i + 2] == "{":
            brace += 1
            buf.append("${")
            i += 2
            continue
        elif c == "{" and brace > 0:
            brace += 1
            buf.append(c)
        elif c == "}" and brace > 0:
            brace -= 1
            buf.append(c)
        elif brace > 0:
            buf.append(c)
        elif c == "(":
            paren += 1
            buf.append(c)
        elif c == ")":
            paren = max(0, paren - 1)  # clamp: a stray ')' must not disable top-level splits
            buf.append(c)
        elif c == sep and paren == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    out.append("".join(buf))
    return out


# --------------------------------------------------------------------------- #
# Evaluate (pool-agnostic: `resolve` is a path -> value callable; `item` is the
# MAP-body-local scope)
# --------------------------------------------------------------------------- #


def eval_binding(segments: list, resolve: Callable[[str], Any], item: Any = None) -> Any:
    """
    Evaluate parsed binding segments to a value.

    A binding that is EXACTLY one `${...}` span resolves to the **typed** value of that
    reference (a float stays a float, a list a list, an object a dict). A span embedded in
    surrounding text — or a plain-text segment — is stringified and concatenated.

    Args:
        segments (`list[_Text | _Coalesce]`):
            The parsed binding from [`parse_binding`][agent_composer.expr.parse_binding].
        resolve (`Callable[[str], Any]`):
            Resolves a reference path to its value (pool-agnostic seam); a missing
            reference resolves to `None`.
        item (`Any`, *optional*, defaults to `None`):
            The MAP-body-local scope for `${item}` / `${item.path}`. `None` means no item
            scope (a `None` element and "no scope" coincide).

    Returns:
        `Any`:
            The typed value for a whole single span; otherwise the rendered string.

    Raises:
        `RequiredError`:
            If a `${ref:?message}` atom is unbound.
    """
    if len(segments) == 1 and isinstance(segments[0], _Coalesce):
        return _eval_coalesce(segments[0], resolve, item)
    parts: list = []
    for seg in segments:
        if isinstance(seg, _Text):
            parts.append(seg.text)
        else:
            v = _eval_coalesce(seg, resolve, item)
            parts.append("" if v is None else str(v))
    return "".join(parts)


def _eval_coalesce(c: _Coalesce, resolve: Callable[[str], Any], item: Any) -> Any:
    for atom in c.atoms:
        v = _eval_atom(atom, resolve, item)
        if v is not None:  # first non-None wins (a present falsy 0/False/"" wins)
            return v
    return None


def _eval_atom(atom: _Atom, resolve: Callable[[str], Any], item: Any) -> Any:
    if isinstance(atom, _Lit):
        return atom.value
    if isinstance(atom, _Ref):
        return _resolve_path(atom.path, resolve, item)
    if isinstance(atom, _Default):
        v = _resolve_path(atom.path, resolve, item)
        if v is not None:
            return v
        d = atom.default
        return _eval_coalesce(d, resolve, item) if isinstance(d, _Coalesce) else d
    if isinstance(atom, _Required):
        v = _resolve_path(atom.path, resolve, item)
        if v is None:
            raise RequiredError(atom.message)
        return v
    raise ExpressionError(f"unknown atom {atom!r}")  # defensive


def _resolve_path(path: str, resolve: Callable[[str], Any], item: Any) -> Any:
    parts = path.split(".")
    if parts[0] == "item":  # MAP-body-local scope (not a pool head)
        if item is None:
            return None
        value = item
        for step in parts[1:]:
            value = value.get(step) if isinstance(value, dict) else None
        return value
    return resolve(path)


# --------------------------------------------------------------------------- #
# Compile-time ref collection — every reference path a binding reads,
# including the one nested-default ref. No evaluation.
# --------------------------------------------------------------------------- #


def binding_refs(segments: list) -> list:
    refs: list = []
    for seg in segments:
        if isinstance(seg, _Coalesce):
            _collect_refs(seg, refs)
    return refs


def _collect_refs(c: _Coalesce, refs: list) -> None:
    for atom in c.atoms:
        if isinstance(atom, _Ref):
            refs.append(atom.path)
        elif isinstance(atom, _Required):
            refs.append(atom.path)
        elif isinstance(atom, _Default):
            refs.append(atom.path)
            if isinstance(atom.default, _Coalesce):
                _collect_refs(atom.default, refs)


def binding_co_skips(source: Any) -> bool:
    """True if this binding co-skips when all its referenced producers are skipped.

    A hard data dependency: a whole-string `${...}` coalesce of refs / ref-defaults
    with NO literal escape (`_Lit`, a literal `_Default`) and NO `:?` (`_Required`). A literal
    fallback runs the node with the literal; a `:?` runs it to fail loud (e07); embedded text
    stringifies an absent ref to ''. None of those co-skip; non-strings never co-skip.
    """
    if not isinstance(source, str):
        return False
    try:
        segments = parse_binding(source)
    except ExpressionError:
        return False
    if len(segments) != 1 or not isinstance(segments[0], _Coalesce):
        return False  # embedded text / plain literal -> stringifies, never co-skips
    for atom in segments[0].atoms:
        if isinstance(atom, _Lit):
            return False  # a literal operand always satisfies the group
        if isinstance(atom, _Required):
            return False  # `:?` -> run & fail (e07), not co-skip
        if isinstance(atom, _Default) and not isinstance(atom.default, _Coalesce):
            return False  # `:-literal` (a ref-default `:-${y}` keeps co-skipping)
    return True


# --------------------------------------------------------------------------- #
# Strict prompt rendering — an AGENT/HUMAN_INPUT prompt against its bound input
# record. A `${...}` span is EITHER a plain dotted reference (`${name.path}`) OR a
# builtin call (`${ fn(${ref}, lit).path }`). Unlike a binding, a prompt is not on the
# graph: it reads inputs already bound to this node, mints no edge, and (the call form)
# evaluates a read-only `TEMPLATE_FNS` formatter at render time — a bounded bend of the
# "all computation is a node" law (see docs/agent-compose-principles.md §4(A)). The
# renderer (`render_template_record`) and the compile-time scope check
# (`prompt_refs` -> `compose.validate`) share ONE span parser so they cannot drift.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _PromptRef:
    """A plain `${name.path}` prompt span — a dotted record reference."""

    path: str


@dataclass(frozen=True)
class _PromptArg:
    """One builtin-call argument. `name` is the keyword (None -> positional). Exactly one
    of `ref` (a `${...}`-wrapped dotted record path) / `literal` is meaningful, per `is_ref`."""

    name: Optional[str]
    ref: Optional[str]
    literal: Any
    is_ref: bool


@dataclass(frozen=True)
class _PromptCall:
    """A `${ callee(args).trailing }` prompt span: a `TEMPLATE_FNS` builtin call with
    ordered `args` and optional dotted access `trailing` on the result."""

    callee: str
    args: tuple  # tuple[_PromptArg, ...]
    trailing: tuple  # tuple[str, ...] — dotted steps after the close paren


def _check_prompt_path(path: str) -> None:
    """A prompt reference path must split into non-empty dotted segments (the charset is
    left permissive — same lenient check the regex renderer used)."""
    parts = [p.strip() for p in path.split(".")]
    if not parts or any(not p for p in parts):
        raise ExpressionError(f"malformed reference ${{{path}}} in prompt")


def _parse_prompt_span(interior: str) -> Union[_PromptRef, _PromptCall]:
    """Parse one `${...}` interior into a plain ref or a builtin call. Raises on a
    malformed span (empty / unbalanced parens / bad dotted access / non-ref non-literal
    arg). Does NOT check the builtin exists or resolve refs — that is the caller's job."""
    s = interior.strip()
    if not s:
        raise ExpressionError("empty ${} span in prompt")
    m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", s)
    if m is None:  # no call syntax -> a plain dotted reference
        _check_prompt_path(s)
        return _PromptRef(s)
    callee = m.group(1)
    open_idx = m.end() - 1
    close_idx = _find_paren_end(s, open_idx + 1)
    if close_idx is None:
        raise ExpressionError(f"unbalanced '(' in prompt call {s!r}")
    args = tuple(
        _parse_prompt_arg(raw)
        for raw in _split_calls_aware(s[open_idx + 1 : close_idx], ",")
        if raw.strip()  # skip `f()` / a trailing comma
    )
    trailing_str = s[close_idx + 1 :].strip()
    trailing: tuple = ()
    if trailing_str:
        if not trailing_str.startswith("."):
            raise ExpressionError(f"unexpected {trailing_str!r} after call in prompt {s!r}")
        steps = [p.strip() for p in trailing_str[1:].split(".")]
        if not steps or any(not p for p in steps):
            raise ExpressionError(f"malformed dotted access {trailing_str!r} in prompt {s!r}")
        trailing = tuple(steps)
    return _PromptCall(callee, args, trailing)


def _parse_prompt_arg(raw: str) -> _PromptArg:
    """One builtin-call arg `[(name=)]value` -> a `_PromptArg`. A top-level `=` with a
    bare-identifier LHS is a keyword; the value is either ONE whole `${ref}` (a record
    reference) or a literal (number/bool/null/quoted) — a bare unwrapped word is rejected
    (record refs MUST be `${...}`-wrapped)."""
    name: Optional[str] = None
    value = raw
    eq_parts = _split_calls_aware(raw, "=")
    if len(eq_parts) >= 2 and re.fullmatch(r"\s*[A-Za-z_][A-Za-z0-9_]*\s*", eq_parts[0]):
        name = eq_parts[0].strip()
        value = "=".join(eq_parts[1:])
    v = value.strip()
    if v.startswith("${"):  # a record reference — must be exactly one whole span
        end = _find_span_end(v, 2)
        if end == len(v) - 1:
            ref = v[2:end].strip()
            _check_prompt_path(ref)
            return _PromptArg(name=name, ref=ref, literal=None, is_ref=True)
    try:
        lit = _parse_literal(v)
    except ExpressionError:
        raise ExpressionError(
            f"prompt call arg {v!r} must be a `${{ref}}` or a literal "
            f"(number/bool/null/quoted string)"
        ) from None
    return _PromptArg(name=name, ref=None, literal=lit, is_ref=False)


def _resolve_record_strict(path: str, record: dict) -> Any:
    """Resolve a dotted `path` against a node's bound input `record`, raising on an
    unknown head, a dotted-walk miss, or a None value (the strict prompt floor)."""
    parts = [p.strip() for p in path.split(".")]
    if not parts or any(not p for p in parts):
        raise ExpressionError(f"malformed reference ${{{path}}} in prompt")
    if parts[0] not in record:
        raise ExpressionError(f"unresolved input reference ${{{path}}} in prompt")
    value: Any = record[parts[0]]
    for step in parts[1:]:
        value = value.get(step) if isinstance(value, dict) else None
        if value is None:
            raise ExpressionError(f"unresolved input reference ${{{path}}} in prompt")
    if value is None:
        raise ExpressionError(f"unresolved input reference ${{{path}}} in prompt")
    return value


def _eval_prompt_span(interior: str, record: dict) -> Any:
    """Evaluate one `${...}` prompt span to its typed value (the caller stringifies)."""
    node = _parse_prompt_span(interior)
    if isinstance(node, _PromptRef):
        return _resolve_record_strict(node.path, record)
    fn = TEMPLATE_FNS.get(node.callee)
    if fn is None:
        raise ExpressionError(f"unknown prompt function {node.callee!r}")
    pos: list = []
    kw: dict = {}
    for a in node.args:
        val = _resolve_record_strict(a.ref, record) if a.is_ref else a.literal
        if a.name is None:
            pos.append(val)
        else:
            kw[a.name] = val
    try:
        value = fn(*pos, **kw)
    except ExpressionError:
        raise
    except Exception as exc:  # a builtin blew up (bad arity, wrong type) — surface loudly
        raise ExpressionError(f"prompt function {node.callee!r} failed: {exc}") from exc
    for step in node.trailing:
        value = value.get(step) if isinstance(value, dict) else None
        if value is None:
            raise ExpressionError(
                f"unresolved dotted access .{step} on {node.callee!r}() result in prompt"
            )
    return value


def render_template_record(text: str, record: dict) -> str:
    """
    Render a strict AGENT / HUMAN_INPUT prompt against its bound input `record`.

    Each `${...}` span is a plain dotted reference (`${name}` / `${name.path}`) or a
    builtin call (`${ render_as_json(${name}, 4) }`, optionally `.field` on the result).
    A reference resolves against `record` (a node's declared inputs); a call invokes the
    named `expr.builtins.TEMPLATE_FNS` formatter over its resolved args. Bare local-input
    refs only — the pool namespaces (`node`/`system`) are not in scope.

    Unlike `evaluate_when_record` (strict CASE), which returns `None`->falsy on a
    dotted miss, this renderer RAISES — the locked strict-prompt floor.

    Args:
        text (`str`):
            The prompt template: literal text interspersed with `${...}` spans.
        record (`dict`):
            The node's bound input record; reference heads must be declared keys.

    Returns:
        `str`:
            The fully rendered prompt with every span substituted by its string value.

    Raises:
        `ExpressionError`:
            On an unbalanced span, an unresolved reference (unknown input, dict-path miss,
            or `None` value), an unknown builtin, or a builtin that fails.
    """
    out: list = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "$" and text[i + 1 : i + 2] == "{":
            j = _find_span_end(text, i + 2)
            if j is None:
                raise ExpressionError(f"unbalanced '${{' in prompt {text!r}")
            out.append(str(_eval_prompt_span(text[i + 2 : j], record)))
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def prompt_refs(text: str) -> list[str]:
    """Every declared-input reference PATH a prompt reads — the compile-time companion to
    `render_template_record`, used by `compose.validate` to name-check prompt scope.

    Returns each plain-span path plus each `${...}`-wrapped builtin-call argument path
    (literals contribute nothing). Raises `ExpressionError` on a malformed span or an
    unknown builtin callee (so the loader rejects both at load time)."""
    refs: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "$" and text[i + 1 : i + 2] == "{":
            j = _find_span_end(text, i + 2)
            if j is None:
                raise ExpressionError(f"unbalanced '${{' in prompt {text!r}")
            node = _parse_prompt_span(text[i + 2 : j])
            if isinstance(node, _PromptRef):
                refs.append(node.path)
            else:
                if node.callee not in TEMPLATE_FNS:
                    raise ExpressionError(f"unknown prompt function {node.callee!r}")
                refs.extend(a.ref for a in node.args if a.is_ref and a.ref is not None)
            i = j + 1
        else:
            i += 1
    return refs


# --------------------------------------------------------------------------- #
# The ONE unified template scanner — splits a text string into literal runs +
# `${...}` spans, parsing ONLY the span interiors with the unified grammar
# (`expr.grammar.parse_expr`) and evaluating them via `expr.expressions.eval_expr`.
#
# This is the template layer OVER the one parser: outside `${...}`, text is LITERAL
# — operator characters (`|`, `+`, `[`, ...) in literal text are NEVER parsed as
# expression operators, which is what makes free text like
# `"stance (positive|negative|neutral)"` safe. Only span interiors go through
# `parse_expr`. `$$` -> a literal `$` everywhere (the universal-escape decision).
#
# It is a NEW `Segment` type, deliberately NOT the legacy `_Text`/`_Coalesce`
# (whose `_Coalesce` holds legacy `_Atom`s, not a `parse_expr` Lark tree): reusing
# them would entangle this path with `parse_binding`. Added ALONGSIDE the legacy
# `render_template_record` / `parse_binding` / `eval_binding` / `prompt_refs`; later
# steps switch callers over and delete the legacy code.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Literal:
    """A literal run of a template — raw text with `$$` already collapsed to `$`.
    Its `text` is emitted verbatim; it is never parsed as an expression."""

    text: str


@dataclass(frozen=True)
class Span:
    """A `${...}` span of a template — its interior parsed via `grammar.parse_expr`.
    `tree` is the resulting `Tree | Token` (a lone atom inlines to a `Token`),
    evaluated by `eval_expr`. ONLY span interiors are parsed."""

    tree: Tree | Token


# A template Segment is a literal run OR a parsed `${...}` span.
Segment = Union[Literal, Span]


def scan_template(text: str) -> list[Segment]:
    """
    Scan a template string into a list of `Segment`s — literal runs + `${...}` spans.

    Splits `text` on `${...}` spans, parsing ONLY each span's interior via
    `expr.grammar.parse_expr`. Literal text between spans is kept RAW (with `$$`
    collapsed to `$`) and is NEVER parsed — so operator characters (`|`, `+`, `[`,
    ...) in free text stay literal. `$$` -> `$` everywhere; a lone `$` not starting
    a `${` span stays literal.

    Args:
        text (`str`):
            The template source: literal text interspersed with `${...}` spans.

    Returns:
        `list[Segment]`:
            The ordered segments — a [`Literal`][agent_composer.expr.template.Literal]
            per literal run and a [`Span`][agent_composer.expr.template.Span] per
            `${...}` (interior already parsed). Adjacent literal runs are never
            emitted separately: each literal run is one `Literal`. A pure-literal
            template is a single `Literal`.

    Raises:
        `ExpressionError`:
            On an unbalanced `${...}` span, or if a span interior fails to parse.
    """
    segments: list = []
    buf: list = []  # accumulates the current literal run (with `$$` -> `$`)
    i, n = 0, len(text)
    while i < n:
        if text[i] == "$" and text[i + 1 : i + 2] == "$":
            buf.append("$")  # `$$` -> a literal `$`
            i += 2
        elif text[i] == "$" and text[i + 1 : i + 2] == "{":
            if buf:
                segments.append(Literal("".join(buf)))
                buf = []
            end = _find_span_end(text, i + 2)  # reuse the shared brace/quote matcher
            if end is None:
                raise ExpressionError(f"unbalanced '${{' in template {text!r}")
            segments.append(Span(grammar.parse_expr(text[i + 2 : end])))
            i = end + 1
        else:
            buf.append(text[i])
            i += 1
    if buf:
        segments.append(Literal("".join(buf)))
    return segments


def eval_template(
    segments: list,
    resolve: Callable[[str], Any],
    item: Any = None,
    mode: ResolveMode = ResolveMode.BINDING_NONE,
) -> Any:
    """
    Evaluate scanned template `segments` to a value.

    A template that is EXACTLY one `${...}` span (no surrounding literal text)
    returns the TYPED value of `eval_expr` (a float stays a float, a list a list, a
    dict a dict). Otherwise every span is stringified and concatenated with the
    literal runs (an embedded span is stringified; `None` -> `""`). A pure-literal
    template returns its literal string. An empty template (`scan_template("") == []`,
    no segments at all) returns `""`.

    Args:
        segments (`list[Segment]`):
            The scan from [`scan_template`][agent_composer.expr.template.scan_template].
        resolve (`Callable[[str], Any]`):
            Resolves a reference path to its value (pool-agnostic seam); a path it
            maps to `None` is a miss.
        item (`Any`, *optional*, defaults to `None`):
            The MAP-body-local scope for `${item}` / `${item.path}`.
        mode (`ResolveMode`, *optional*, defaults to `BINDING_NONE`):
            How a missing reference is treated inside a span (see `eval_expr`).

    Returns:
        `Any`:
            The typed value for a whole-single-span template; otherwise the rendered
            string (pure-literal -> the literal string).

    Raises:
        `ExpressionError`:
            On any error `eval_expr` raises for a span interior.
        `RequiredError`:
            When a span's `head :? message` required atom misses / is None.
    """
    if len(segments) == 1 and isinstance(segments[0], Span):
        return eval_expr(segments[0].tree, resolve, item=item, mode=mode)
    parts: list = []
    for seg in segments:
        if isinstance(seg, Literal):
            parts.append(seg.text)
        else:  # a Span embedded in surrounding text -> stringified (None -> "")
            v = eval_expr(seg.tree, resolve, item=item, mode=mode)
            parts.append("" if v is None else str(v))
    return "".join(parts)


def template_refs(segments: list) -> list[str]:
    """
    Collect every reference PATH a template reads — union (source order) of
    `expr_refs` over the span segments.

    Literal runs contribute nothing. Matches the no-dedupe / source-order convention
    of [`expr_refs`][agent_composer.expr.expressions.expr_refs], so callers switched
    from `binding_refs` / `prompt_refs` see the same shape.

    Args:
        segments (`list[Segment]`):
            The scan from [`scan_template`][agent_composer.expr.template.scan_template].

    Returns:
        `list[str]`:
            The reference paths across all span segments, in source order (NOT
            deduped).
    """
    refs: list = []
    for seg in segments:
        if isinstance(seg, Span):
            refs.extend(expr_refs(seg.tree))
    return refs
