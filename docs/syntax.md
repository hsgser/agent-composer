# Flow syntax

A flow is a **function**: a typed `input:`, a graph of `nodes:`, and a typed
`output:`. You never draw edges — the graph is *inferred* from the `${...}`
references between nodes. The human owns the structure; the LLM only fills the
leaf boxes.

## The flow shape

A flow file is Docker-Compose-shaped: metadata scalars at the top, then the
interface and body as top-level sections.

```yaml
id: momentum               # stable identifier
name: momentum             # display name
description: ...           # optional, one line
typedefs: { ... }          # optional — named/composed types
input:   { ... }           # parameters — typed
nodes:   { ... }           # body — a map keyed by node id
output:  { ... }           # return — bindings (one value, or a multi-field object)
asserts: [ ... ]           # optional — boolean invariants
```

There is **no `edges:` block**, no `__start__`/`__end__`, no per-node `id:`, and
no body wrappers — a node body is flat.

## References — naming a value

Everywhere you wire data you use a `${...}` reference:

| Write | Means |
|-------|-------|
| `${input.X}` | field `X` of the flow's input |
| `${node.output}` | node `node`'s whole value |
| `${node.output.field}` | dot into an object value |
| `${name}` (bare) | **inside an AGENT/HUMAN_INPUT `prompt:` or a `case` `when:`** — that node's own declared input |

A whole-string `${ref}` resolves to the **typed value**; embedded in surrounding
text it is stringified.

!!! important "Prompts see only local inputs"
    Inside a `prompt:` you may reference only names the node declares in its own
    `input:` block — written bare, like `${name}`. Pool references
    (`${input.x}`, `${other.output}`) belong in the `input:` block, not the
    prompt. Bind it there, then refer to the local name in the prompt.

### Operators inside `${...}`

| Form | Meaning |
|------|---------|
| `${X:-default}` | value, else `default` if absent |
| `${X:?msg}` | required — fail with `msg` if absent |
| `${a \| b \| c}` | first present among peers (n-ary coalesce — for branch joins) |
| `$$` | a literal `$` |

Nesting is allowed: `${a:-${b:-lit}}`.

## Types

The type vocabulary is Python typing. Scalars: `str`, `int`, `float`, `bool`,
`date`, `datetime`, `object`, `None`. Containers/forms: `list[X]`, `Optional[X]`,
`Literal[...]` (an enum of tags), and named **records** (dataclass-style).

Name reusable types in `typedefs:`:

```yaml
typedefs:
  Ticker: str                          # alias
  Basket: list[Ticker]                 # aliases compose
  Decision: Literal[go, no_go, wait]   # enum — one of these tags
  Signal:                              # record — fields recurse
    score: float
    note: Optional[str]                # nullable field
```

`Optional[X]` (nullable) and a `= default` (omission-fill) are orthogonal: a
required input has neither; `lookback: int = 30` defaults when omitted;
`Optional[date]` omitted resolves to null.

## Node kinds

Every node has a `kind:`. The set is closed — you compose flows from these, you
do not define new kinds.

### `agent` — an LLM leaf

```yaml
classify:
  kind: agent
  input:
    text: ${input.text}        # bindings — these infer the data edges
  output: Literal[positive, neutral, negative]
  prompt: |-
    Classify the sentiment of: ${text}
    Answer with exactly one of: positive, neutral, negative.
```

!!! warning "AGENT outputs are text — `str` or `Literal[...]` only"
    An AGENT returns plain text, which the engine binds against the declared
    `output:` with no JSON/structured parse. So an AGENT's `output:` can be
    `str` or a `Literal[...]` enum (the model answers with one tag) — but
    **not** a record, `float`, `int`, `bool`, or `object`. To produce a
    structured or numeric value, compute it in a `code` / `model` node that
    consumes the agent's text.

A node can pin its own provider/model; otherwise the environment defaults apply
(see [Installation](installation.md)).

#### `llm_config` — the model-selection cascade

Model selection cascades **per field, most-specific wins**. Each field (provider,
model, temperature, …) is resolved independently: an agent fills only the fields
it leaves unset from the layer outside it. Precedence, most specific first:

1. the agent's own `llm_config:`
2. the enclosing (sub)flow's `llm_config:`, then each parent flow outward
3. the CLI `--provider` / `--model` flags (`ac run … --provider anthropic`)
4. the environment defaults baked in by `model_from_config`

A flow can set defaults for every agent under it with a top-level `llm_config:`:

```yaml
llm_config:                     # flow layer — every agent inherits these
  provider: anthropic
  temperature: 0.2
nodes:
  drafter:
    kind: agent
    prompt: Draft a summary.
    llm_config:
      model: claude-opus-4-8    # fills the one field the flow leaves unset
```

The CLI flags are the *outermost* layer — they fill gaps, they do **not** override
an agent or flow that set the field. To take a node out of the cascade entirely,
set `inherit: false` in its `llm_config:` — the node then uses its own dict only,
ignoring all outer layers:

```yaml
  grader:
    kind: agent
    prompt: Grade it.
    llm_config:
      provider: openai
      model: gpt-5.5
      inherit: false            # own dict only — no flow/CLI layers
```

### `code` / `model` / `tool` — the other leaves

These are the non-LLM computational leaves — a Python callable, an ML model, or
a registered tool. They take typed `input:` bindings and declare a typed
`output:` (which, unlike an AGENT, may be any type).

```yaml
verdict:
  kind: code
  input:
    s: ${score.output.signal}
  output: str
  code: pkg.mod:fn             # module:function
```

### `case` — branching

A `case` node **routes only** — it has no `input:`. Exactly one branch runs; the
others are skipped (their references resolve to null). Join the branches back
with a coalesce.

Simple form — match a value:

```yaml
route:
  kind: case
  on: ${synth.output}          # a Literal value
  cases:
    - when: go
      then: go_brief
    - when: no_go
      then: no_go_brief
  else: more_info_brief        # required unless the cases are exhaustive
```

Searched form — first true `when:` wins (a boolean expression, no `on:`):

```yaml
gate:
  kind: case
  cases:
    - when: "${score.output.signal} >= 0.5"
      then: bullish
  else: cautious
```

Routing on a `Literal` is **exhaustiveness-checked**: omitting a tag with no
`else:` is a compile error. Join the branches:

```yaml
output: ${bullish.output | cautious.output}
```

### `human_input` — a deterministic human gate

A `human_input` node **always** suspends the run at a fixed point and waits for a
typed answer from the human. The answer is validated against the declared
`output:` before the flow continues.

```yaml
approve:
  kind: human_input
  input:
    plan: ${draft.output}      # context the prompt may reference (bare ${plan})
  prompt: |-
    Here is the plan:

    ${plan}

    Approve as-is, or revise? (approve / revise)
  output: Approval             # a typed answer, e.g. a Literal enum
```

### `wait` — a timed pause

```yaml
settle:
  kind: wait
  until: ${input.as_of}
  # downstream nodes order after it with depends_on: [settle]
```

### `call` / `map` — composition over child flows

`call` runs a child flow once; `map` runs it over a list (`${item}` is the
current element). The child is itself a flow file.

```yaml
each:
  kind: map
  over: ${input.tickers}       # a list[T]
  call: child_flow             # node value is list[U]
  parallel: true
  input:
    ticker: ${item}
```

## Effects from inside an agent — the `ask_user` control

A `tool_calling` agent can be granted the `ask_user` **control**. Unlike
`human_input` (which always pauses), `ask_user` is *model-chosen*: the agent
suspends to ask the human **only if** it decides it needs a fact it can't supply,
then resumes with the answer fed back as the tool result.

```yaml
assistant:
  kind: agent
  mode: tool_calling           # the loop — required to call a control
  controls: [ask_user]         # enable the capability
  input:
    request: ${input.request}
  output: str
  prompt: |-
    The user asks: ${request}
    If a detail essential to a good answer is missing, call ask_user ONCE to get it.
    Otherwise answer directly.
```

For a *guaranteed* gate use `human_input`; for "ask only if needed" use
`ask_user`.

## Expression contexts

Three places take expressions, with different power:

- **Bindings** (`input:` / `output:` values): `${ref}`, a literal, `:-`/`:?`,
  and `|`. **No arithmetic** — transforms belong in nodes.
- **`when:` / `asserts:`**: boolean expressions — `== != < <= > >=`, `in` /
  `not in`, `and` / `or` / `not`, parentheses, over operands that may use
  arithmetic (`+ - * / %`). No function calls.
- **Prompts**: free text with embedded bare `${name}` (stringified).

> Bindings wire, conditions test, nodes compute.

## Asserts

`asserts:` are boolean invariants; a false (or raising) one fails the run loudly.
A top-level `asserts:` runs over `${input.X}` (a boundary check, before any node)
or `${node.output}` (a post check, after the terminal).

```yaml
asserts:
  - ${synth.output} in ["go", "no_go", "needs_more_info"]
```

## Next

- [Examples](examples.md) — these constructs in working flows.
- [Python API](api.md) — run and resume flows from code.
