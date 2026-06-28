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

## Install

> **Early stage.** Agent Composer is under active development and not yet on PyPI.
> The API, YAML surface, and CLI may move or change quickly between commits — pin to a
> commit if you need stability.

Install directly from the repository:

```console
pip install "git+https://github.com/ngocbh/agent-composer.git"
```

Or clone and install in editable mode:

```console
git clone https://github.com/ngocbh/agent-composer.git
cd agent-composer
pip install -e .
```

Provider SDKs are optional extras — install the one(s) you use:

```console
pip install "agent-composer[anthropic] @ git+https://github.com/ngocbh/agent-composer.git"   # Claude
pip install "agent-composer[openai]    @ git+https://github.com/ngocbh/agent-composer.git"   # GPT
pip install "agent-composer[google]    @ git+https://github.com/ngocbh/agent-composer.git"   # Gemini
pip install "agent-composer[ollama]    @ git+https://github.com/ngocbh/agent-composer.git"   # local models
pip install "agent-composer[all]       @ git+https://github.com/ngocbh/agent-composer.git"   # everything
```

From a clone, the extras are simply `pip install -e ".[anthropic]"`, `".[all]"`, etc.

The core (engine + CLI) installs with no provider SDK; importing a provider you
haven't installed raises a clear `pip install agent-composer[...]` hint.

## The `ac` CLI

```console
ac run FLOW.yaml [--input k=v]... [--inputs inputs.json] [--quiet]
```

- `--input k=v` — set one input (repeatable). Values are coerced to each input's
  declared type.
- `--inputs file.json` — load inputs from a JSON object. `--input` flags override
  individual keys.
- Any required input still missing is **prompted interactively**.
- A flow that suspends on a `HUMAN_INPUT` / `WAIT` node is **resumed interactively** —
  each pause prompts for the awaited value and the run continues to completion.

### Choosing a provider/model

The default provider and model are read from the environment:

```console
export AGENT_COMPOSER_DEFAULT_PROVIDER=anthropic        # or openai / google / ollama
export AGENT_COMPOSER_DEFAULT_MODEL=claude-sonnet-4-5
export ANTHROPIC_API_KEY=...                            # provider's own key var
```

For a local Ollama endpoint:

```console
export AGENT_COMPOSER_DEFAULT_PROVIDER=ollama
export AGENT_COMPOSER_DEFAULT_MODEL=llama3.2:3b
export OLLAMA_BASE_URL=http://localhost:11434
ac run examples/hello.yaml --input name=Ada
```

## Examples

The [`examples/`](examples/) directory ships a few generic flows:

- `hello.yaml` — the smallest agent flow (one AGENT, string in/out).
- `summarize.yaml` — condense a block of text into one sentence.
- `classify.yaml` — label text with a constrained `Literal[...]` output.
- `triage-ticket.yaml` — extract a structured record from a support message, then draft a reply.
- `decision-brief.yaml` — fan-out to three angles, pick a verdict, route, and finalize.
- `ask-user.yaml` / `human-approval.yaml` — the model-chosen vs. always-on human-in-the-loop pauses.

## Use it as a library

```python
from agent_composer import load_flow, run_flow

loaded = load_flow(open("hello.yaml").read(), search_paths=["."])
result = run_flow(loaded, {"name": "Ada"})
print(result.status, result.output)
```

## Develop & test

```console
pip install -e ".[all,dev]"
pytest
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
