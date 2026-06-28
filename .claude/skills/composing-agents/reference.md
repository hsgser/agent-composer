# Reference — composing flows

Deep-dive companion to [`SKILL.md`](SKILL.md). The skill is the authoring
workflow; this is the lookup sheet (operators, contexts, type forms, recipes,
gotchas) plus the [`templates/`](templates/) index. The authoritative grammar is
[`docs/syntax.md`](../../../docs/syntax.md); runnable end-to-end flows are in
[`examples/`](../../../examples).

## Templates (copy one as a starting point)

Each file in [`templates/`](templates/) is a minimal, loadable flow for one shape.
Copy, rename, and edit.

| Template | Shape it shows |
|----------|----------------|
| [`minimal.yaml`](templates/minimal.yaml) | one AGENT, `str` in/out |
| [`pipeline.yaml`](templates/pipeline.yaml) | AGENT (text) → CODE (typed record) — how to get structured output |
| [`branching.yaml`](templates/branching.yaml) | classify → `case` route → `\|` join |
| [`tool-use.yaml`](templates/tool-use.yaml) | a `tool` node (no LLM) feeding an AGENT |
| [`human-in-loop.yaml`](templates/human-in-loop.yaml) | a `human_input` pause/gate |
| [`child-summarize.yaml`](templates/child-summarize.yaml) | a reusable CHILD flow |
| [`call-child.yaml`](templates/call-child.yaml) | `call` a sibling flow once (via `uses:`) |
| [`map-fanout.yaml`](templates/map-fanout.yaml) | `map` a child over a list, in parallel |
| [`llm-config-cascade.yaml`](templates/llm-config-cascade.yaml) | flow-level `llm_config:`, per-node override, `inherit: false` |

`call-child.yaml` and `map-fanout.yaml` depend on `child-summarize.yaml` being on
the search path — run them from the `templates/` dir (or pass the dir to
`load_flow(search_paths=...)`).

## Operators inside `${...}`

| Form | Meaning |
|------|---------|
| `${X:-default}` | value, else `default` if absent |
| `${X:?msg}` | required — fail with `msg` if absent |
| `${a \| b \| c}` | first present among peers — **the branch-join coalesce** |
| `$$` | a literal `$` |

Nesting is allowed: `${a:-${b:-lit}}`. A whole-string `${ref}` resolves to the
**typed value**; embedded in surrounding text it is **stringified**.

## The three expression contexts (different power)

| Context | Where | What's allowed |
|---------|-------|----------------|
| **Bindings** | `input:` / `output:` values | `${ref}`, a literal, `:-` / `:?` / `\|`. **No arithmetic, no function calls.** |
| **Conditions** | `when:` / `asserts:` | boolean: `== != < <= > >=`, `in`/`not in`, `and`/`or`/`not`, parens; operands may use `+ - * / %`. **No function calls.** |
| **Prompts** | `prompt:` text | free text with embedded bare `${name}` (stringified) |

> Bindings wire, conditions test, nodes compute. Any transform belongs in a `code`
> node, not in an expression.

## Type forms

Python typing vocabulary. Scalars: `str`, `int`, `float`, `bool`, `date`,
`datetime`, `object`, `None`.

| Form | Example | Notes |
|------|---------|-------|
| list | `list[str]` | |
| nullable | `Optional[str]` | may be `null` |
| default-fill | `lookback: int = 30` | filled when the input is omitted |
| enum | `Literal[go, no_go, wait]` | one of these tags |
| alias | `Basket: list[str]` | aliases compose |
| record | (see below) | fields recurse |

```yaml
typedefs:
  Signal:
    score: float
    note: Optional[str]
```

`Optional[X]` (nullable) and `= default` (omission-fill) are **orthogonal** —
nullable says the value may be null; default says what to use when the input
isn't supplied at all.

## Recipes

**Get a structured / numeric value out of an agent.** An AGENT returns text only.
Feed its `str` into a `code` node that parses text → the typed shape (see
`pipeline.yaml`). Never declare a record/`float`/`int` as an AGENT `output:`.

**Branch and rejoin.** A `case` runs exactly one branch; the others skip and their
refs resolve to null. Always coalesce the branches back with `${a | b | c}` (see
`branching.yaml`). Routing on a `Literal` is exhaustiveness-checked — cover every
tag or add an `else:`.

**Order without data flow.** When node B must run after A but consumes no value
from it (a `wait`, a side-effecting tool), use `depends_on: [a]` (co-skips B if A
skipped) or `runs_after: [a]` (orders only; B still runs).

**Ask the human.** For a *guaranteed* gate use a `human_input` node (always
pauses). For "ask only if the model decides it needs to", give a `tool_calling`
agent `controls: [ask_user]`.

**Reuse a sub-flow.** Factor the repeated work into its own flow file, bind it with
`uses: <alias>: <filename>`, and `call:` it (once) or `map:` it (per list element).
Reference its object fields downstream as `${node.output.field}`.

## Gotchas

- **Inline `{ ... }` maps + `${...}` need quotes.** In an inline flow mapping the
  `}` in `${input.x}` closes the map early. Either quote the value
  (`input: {x: "${input.x}"}`) or use block form (preferred):
  ```yaml
  input:
    x: ${input.x}
  ```
- **AGENT `output:` is `str` or `Literal[...]` only** — no record/number/bool/object.
- **Prompts see only LOCAL inputs.** Inside `prompt:` you may reference only names
  the node declares in its own `input:` block, written bare (`${name}`). Pool refs
  (`${input.x}`, `${other.output}`) go in the `input:` block first.
- **No `edges:` block, no per-node `id:`, no body wrappers.** The graph is inferred
  from `${...}` references; a node body is flat.
- **`call:` resolves defs-first, else a `uses:` alias** (a sibling file by name on
  the search path). `alias@v1` adds a version guard.
- **MODEL nodes aren't wired** — `kind: model` parses but running one raises. Use
  `code` for deterministic compute.

## Model selection — the `llm_config` cascade

Model fields resolve **per field, most-specific wins**. An agent fills only the fields
it leaves unset from the layer outside it. Precedence (most specific first):

1. the agent's own `llm_config:`
2. the enclosing (sub)flow's `llm_config:`, then each parent flow outward
3. the CLI `--provider` / `--model` flags
4. env defaults in `model_from_config`

Set flow-wide defaults with a top-level `llm_config:`; override one field per node;
opt a node out of the whole cascade with `inherit: false` (own dict only). See
[`llm-config-cascade.yaml`](templates/llm-config-cascade.yaml).

```yaml
llm_config: {provider: anthropic, temperature: 0.2}   # flow layer
nodes:
  a: {kind: agent, prompt: hi, llm_config: {model: claude-opus-4-8}}        # fills `model`
  b: {kind: agent, prompt: hi, llm_config: {provider: openai, model: gpt-5.5, inherit: false}}
```

## Validate

```bash
ac run <flow>.yaml --input k=v          # loads, then runs (prompts for missing required inputs)
```
From Python: `load_flow(text, search_paths=[flow_dir])` loads + compiles without a
model. A flow with only `code`/`tool` nodes runs with no provider; any `agent` node
needs a provider/model configured.
