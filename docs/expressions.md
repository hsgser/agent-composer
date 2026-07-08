# Expressions

> This is the `${...}` engine the code implements today — the `expr` package
> (`grammar.py` + `expressions.py` + `template.py` + `builtins.py`). It sits above
> [`typesys`](typing.md) and below `nodes` / `compile` on the layer ladder, and is
> **pool-agnostic**: every layer takes a `resolve` callable, never the pool itself.
> For the authoring surface (what you may write inside `${...}`), see
> [Flow syntax → References](syntax.md#references--naming-a-value).

## What `${...}` is?

Everywhere a flow wires data — a node's input `from:`, a `when:` condition, a
flow's `outputs:`, an agent `prompt:` — the author writes a `${...}`. Historically
each of those places grew its *own* little dialect: bindings had coalesce and
defaults, `when:` had booleans and arithmetic, prompts had builtin calls. Three
grammars, three evaluators, three ways to drift.

The `expr` package is the **one** `${...}` engine that replaced all three. There
is a single grammar, a single evaluator, and a single compile-time reference walk;
every context (binding, condition, prompt) is a thin caller over them. It is built
as three stacked layers, plus a registry of pure builtins:

```
   grammar.py     PARSE ONLY — one Lark grammar → a parse tree     (parse_expr)
      │
      ▼
   expressions.py evaluate a tree against a `resolve` callable      (eval_expr)
      │           + the compile-time ref-walk (expr_refs)
      │           + the ref-rewriter (rewrite_expr_refs)
      │           + the when:/asserts condition surface
      ▼
   template.py    the ${...} scanner OVER the parser: split text    (scan_template,
                  into literal runs + spans, drive the engine per     eval_binding,
                  span; also strict prompt rendering                  render_template_record)

   builtins.py    TEMPLATE_FNS — the pure formatters a prompt call may invoke
```

## Layer 1 — `grammar.py`: one grammar, parse only

`parse_expr(text)` turns an expression into a Lark LALR parse tree and does
**nothing else** — no evaluation, no pool. It is the shared front end for every
downstream consumer. One grammar parses every construct:

| Form | Example |
|------|---------|
| reference | `a`, `a.b.c`, `${a}` (wrapped, back-compat), `node#0.output` (graph-expansion segments) |
| arithmetic | `a + 1`, `a * b`, `-x`, `a ** 2` (`+ - * / %`, `**`) |
| comparison | `a == b`, `x < 5`, `x in [1, 2]`, `x not in ys` |
| boolean | `a and b`, `a or b`, `not a` |
| list literal | `[1, "x", a]` (elements are themselves expressions) |
| builtin call | `upper(name)`, `join(items, ", ")`, `fn(x).field` (dotted access on the result) |
| coalesce / default / required | `a \| b \| c`, `a :- d`, `a :? "msg"` |

Precedence, lowest to highest:

```
coalesce (|) → default/required (:- / :?) → or → and → not
→ comparison (incl. in / not in) → sum → product → unary minus → power (**) → atom
```

Two design points are load-bearing:

- **One shared `NAME` terminal with an optional call-suffix** (the "C1" fix). A
  naive grammar with a separate bare-ref terminal *and* a separate call rule does
  not parse under LALR — the lexer matches `upper` as a ref before it can see the
  `(`. The fix is one `NAME`, then dotted `trailer`s, then an optional
  `call_suffix`: no suffix ⇒ a reference; a suffix ⇒ a builtin call (bare callee
  only — `a.b(x)` is rejected in `parse_expr`).
- **Charset: `#` and `/` are in `NAME`, `-` is not.** Runtime graph expansion
  mints id segments like `node#0` and `def/child`, so those characters must lex
  inside a reference. `-` is excluded so `a - b` is subtraction, not the single
  identifier `a-b`.

## Layer 2 — `expressions.py`: the one evaluator

`eval_expr(tree, resolve, item=None, mode=...)` walks a parse tree and produces a
value. It is the single evaluator for **every** `${...}` construct — references,
arithmetic and comparisons over values, boolean combinators, list literals,
coalesce/default/required, and pure builtin calls. It is pool-agnostic: it calls
the `resolve` callable to turn a reference path into a value, so the same
evaluator serves the pool path, the record path (a prompt's local inputs), and the
map-body `item` scope.

### Three locked semantics

These three rules are fixed because `case default` routing depends on them:

1. A **missing reference is `None`** (in the non-strict modes).
2. **Arithmetic over that `None` raises** a loud `ExpressionError` (a wrapped
   `TypeError`) — a missing operand never silently becomes `0`.
3. An **ordered comparison with a `None` operand is `False`** (missing → falsy),
   so an absent value routes to `default` rather than erroring.

### `ResolveMode` — how a miss is treated

A missing reference means different things in different contexts, so the caller
picks a mode:

| Mode | A missing reference… | Used by |
|------|----------------------|---------|
| `BINDING_NONE` | becomes `None` (a coalesce / default may then fire) | binding values (`eval_binding`) |
| `CONDITION_FALSY` | becomes `None`, falsy through comparisons | `when:` / `asserts:` predicates |
| `STRICT_RAISE` | raises — no silent blank | strict prompt rendering |

Internally a miss is a distinct `_MISSING` sentinel (not a resolved `None`), so
`default` / `required` can fire on a genuine miss; the sentinel is mapped to
`None` or a raise at the value boundary per mode.

### Two safety rules

- **Dotted access is dict-key lookup only** — never `getattr`. So `${x.__class__}`
  can never reach a Python attribute; it is just a missing key. This is
  safety-critical.
- **Builtin calls dispatch through `TEMPLATE_FNS` only** — no arbitrary callable is
  reachable from an expression.

### Value ops, not number-only

Arithmetic in `eval_expr` runs on the resolved **values** with Python semantics —
so `str + str` concatenates and `list + list` extends (`${xs + [item]}`),
unlike the strict number-only gate the legacy `when:` used.

### The compile-time companions

The same parse tree feeds two compile-time walks that never evaluate:

- **`expr_refs(tree)`** collects every reference-leaf path an expression reads (a
  builtin *callee* contributes no ref, but its argument refs do). This is how the
  compiler infers the data edges of the graph — a `from:` that reads
  `${a.output}` mints an edge from `a`.
- **`rewrite_expr_refs(text, rename)`** rewrites only the reference *leaves* of an
  expression, splicing by source position — operators, literals, the builtin
  callee, and whitespace are preserved verbatim. This is what re-namespaces refs
  when a child flow is inlined (call / loop / map), where a flat `${...}` regex
  would mangle a whole-span `${a > 5}` or miss a bare `a > 5`.

### The condition surface

`when:` / `until:` / `while:` / `asserts:` are the same grammar, admitting **three
spellings that all parse and evaluate identically**: a bare expression `a > 5`, a
mixed `${a} > 5`, and a whole-span `${a > 5}`. `evaluate_when` /
`first_failing_assert` (pool-based) and `evaluate_when_record` (a prompt/CASE
node's bound inputs) route through this one path, so the spellings can never drift.

## Layer 3 — `template.py`: the `${...}` scanner over the parser

A binding value or a prompt is not a bare expression — it is **text with `${...}`
spans embedded in it**. `scan_template(text)` splits the text into literal runs and
spans, parsing **only** the span interiors with `parse_expr`. That split is what
keeps free text safe: operator characters (`|`, `+`, `[`) in literal text are
never treated as operators, so a prompt line like `stance (positive|negative)`
stays literal. `$$` is the universal escape for a literal `$`.

Evaluation then follows one rule:

- a value that is **exactly one** `${...}` span resolves to the **typed** value of
  that span (a float stays a float, a list a list, an object a dict);
- a span **embedded** in surrounding text is **stringified** into it;
- text with no span is a plain literal.

**Compile-time output typing mirrors this rule.** When the loader needs the Type of a
flow-output binding (to check the loop `'a -> 'a` record contract, a `call` codomain, or a
child signature), `_output_value_type` (in `compose/build.py`) infers it from the binding's
shape: a **single lone span** with a single resolvable ref is a typed passthrough (its
referenced Type); a **concatenating template** — literal text around spans, or two or more
spans — is always a `str`, because `eval_template` joins it; a **pure literal** or a
single-span **multi-ref** expression (e.g. `${a + b}`) stays opaque (`None`, lenient — a
literal is still coercible at the boundary). This is what lets a loop body grow a carried
`str` field with a plain `${...}` template binding, no CODE fold node required.

The public entry points are thin wrappers over the scanner + `eval_expr`:

| Function | Purpose |
|----------|---------|
| `eval_binding(source, resolve, item)` | evaluate a binding value (`from:`, `outputs:`) in `BINDING_NONE` mode |
| `eval_template(segments, ...)` | the scanned-segments form under the hood |
| `expr_refs_of(source)` | the binding-value ref-walk (union of `expr_refs` over spans) — compile-time edge inference |
| `rewrite_template_refs(source, rename)` | rewrite refs across all spans (child-flow inlining) |
| `render_template_record(text, record)` | render a strict AGENT/HUMAN_INPUT prompt against its bound inputs, in `STRICT_RAISE` mode |
| `prompt_refs(text)` | the prompt ref-walk + unknown-builtin rejection (compile-time scope check) |

A **prompt** is the strict context: it reads only the node's own declared inputs
(bare `${name}`, not pool namespaces), it renders in `STRICT_RAISE` mode (a
missing reference is an error, never a silent blank), and it may call a builtin.

## `builtins.py` — the prompt formatters

A prompt `${...}` span may be a plain reference *or* a call to a pure builtin.
`TEMPLATE_FNS` maps a name to a value→value formatter, invoked at render time over
the node's already-bound inputs. This is a deliberate, bounded bend of the
"all computation is a node" law: builtins are read-only string formatting over a
node's *own* inputs, mint no graph node or edge, and are unavailable in
`from:` / `when:` / bindings. The current set:

| Builtin | Does |
|---------|------|
| `render_as_json(value, indent=2)` | pretty-print a value as a JSON block (the headline formatter) |
| `join(value, sep="\n")` | join an iterable's (stringified) elements |
| `upper(s)` / `lower(s)` | case-fold |

New builtins register with `@register_template_fn()`.

## Why this shape

- **One grammar, one evaluator, one ref-walk.** Every `${...}` context is a thin
  caller, so a binding, a condition, and a prompt can never parse the same text
  differently.
- **Pool-agnostic.** Each layer takes a `resolve` callable, so the exact same
  engine serves the pool, a node's local input record, and the map-body `item`
  scope — and `expr` never imports `nodes` / `compile` / `runtime`.
- **Parse-time / eval-time separation.** `grammar.py` only parses; the same tree
  feeds evaluation *and* the two compile-time walks (edge inference, ref
  rewriting), so the graph the compiler infers matches exactly what the runtime
  evaluates.
- **Safe by construction.** Dict-only dotted access and a closed builtin registry
  keep an author expression from ever reaching arbitrary Python.
