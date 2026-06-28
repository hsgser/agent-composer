# The Agent Composer

**Bridging the trust gap between humans and agents.**

Hand an agent a complex task and it improvises a plan on the fly — calling tools,
branching, looping — in whatever shape the context happens to produce. That flexibility
is also the problem: the workflow is *opaque*. You don't see the plan the agent chose,
you can't tell whether it has a bug, and the next run might quietly do something else.
When the stakes are real, "it usually works" isn't trust.

The Agent Composer makes the workflow a **first-class artifact that both you and the
model can read**. Instead of the agent inventing its plan at runtime, the flow is
written out as a small Docker-Compose-shaped YAML file — by you, by an LLM, or by the
two of you together. You can see exactly what runs, inspect it for bugs, and refine it
after an error; so can the model. The human owns the graph; the LLMs only fill the leaf
boxes — they never rewrite the structure at runtime.

A flow is a function: it has a typed `input:`, a graph of `nodes:`, and an `output:`.
The graph between nodes is *inferred* from the `${...}` references — you never draw
edges by hand.

```yaml
# hello.yaml
id: hello
name: hello
input:
  name: str
nodes:
  greet:
    kind: agent
    input:
      name: ${input.name}
    output: str
    prompt: |-
      Write a short, warm one-sentence greeting addressed to ${name}.
output: ${greet.output}
```

```console
$ ac run hello.yaml --input name=Ada
Hello, Ada — it's wonderful to have you here!
```

## Why this shape

- **The workflow is readable** — the flow *is* the plan, in plain YAML. You can review
  it before it runs, spot a bug in the structure, and refine it after an error — and an
  LLM can do the same, because the surface is small and explicit.
- **The structure is fixed by the author** — the LLM fills leaf boxes; it does not
  rewrite the graph. The same flow runs the same way every time, so a fix stays fixed.
- **A flow is a function** — typed inputs in, typed outputs out, nothing hidden. An
  agent is just a flow whose leaf computation happens to be an LLM loop.
- **Flows compose** — a node can *be* another flow, nested to any depth.
- **Pure at the boundary** — a node *returns* its output and the engine *binds* it; a
  node never mutates shared state. Outputs are immutable, typed, serializable values.
  That referential transparency is what makes runs reproducible, checkpointable, and
  resumable.

## Where to go next

<div class="grid cards" markdown>

- :material-download: **[Installation](installation.md)** — `pip install agent-composer`, provider extras, and picking a model.
- :material-console: **[The `ac` CLI](cli.md)** — run a flow from the terminal, supply inputs, resume human pauses.
- :material-file-code: **[Flow syntax](syntax.md)** — the full Compose-YAML reference: types, `${...}` refs, node kinds, `case`, coalesce, asserts.
- :material-lightbulb: **[Examples](examples.md)** — walk through the flows that ship in `examples/`.
- :material-language-python: **[Python API](api.md)** — use the engine as a library (`load_flow` / `run_flow` / `resume_flow`).

</div>
