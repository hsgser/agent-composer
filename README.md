# Agent Compose

**Deterministic workflows of agents.** Agent Compose is a small engine for composing
LLM agents, plain Python code, ML models, and tools into a single runnable *flow*,
described in a Docker-Compose-shaped YAML file. You decide the structure; the LLMs
fill the leaf boxes.

A flow is a function: it has typed `input:`, a graph of `nodes:`, and an `output:`.
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

```console
pip install agent-composer
```

Provider SDKs are optional extras — install the one(s) you use:

```console
pip install "agent-composer[anthropic]"   # Claude
pip install "agent-composer[openai]"      # GPT
pip install "agent-composer[google]"      # Gemini
pip install "agent-composer[ollama]"      # local models
pip install "agent-composer[all]"         # everything
```

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
export AGENT_COMPOSE_DEFAULT_PROVIDER=anthropic        # or openai / google / ollama
export AGENT_COMPOSE_DEFAULT_MODEL=claude-sonnet-4-5
export ANTHROPIC_API_KEY=...                            # provider's own key var
```

For a local Ollama endpoint:

```console
export AGENT_COMPOSE_DEFAULT_PROVIDER=ollama
export AGENT_COMPOSE_DEFAULT_MODEL=llama3.2:3b
export OLLAMA_BASE_URL=http://localhost:11434
ac run examples/hello.yaml --input name=Ada
```

## Examples

The [`examples/`](examples/) directory ships a few generic flows:

- `hello.yaml` — the smallest agent flow (one AGENT, string in/out).
- `summarize.yaml` — condense a block of text into one sentence.
- `classify.yaml` — label text with a constrained `Literal[...]` output.

## Use it as a library

```python
from agent_compose import load_flow, run_flow

loaded = load_flow(open("hello.yaml").read(), search_paths=["."])
result = run_flow(loaded, {"name": "Ada"})
print(result.status, result.output)
```

## Develop & test

```console
pip install -e ".[all,dev]"
pytest
```

## Publish

Releases are published to PyPI automatically by GitHub Actions
([`.github/workflows/publish.yml`](.github/workflows/publish.yml)) whenever a
GitHub **Release** is published. It uses PyPI **Trusted Publishing** (OIDC), so no
API token or secret is stored in the repo.

**One-time PyPI setup** (https://pypi.org/manage/account/publishing/ → "Add a pending publisher"):

| Field | Value |
|-------|-------|
| PyPI Project Name | `agent-composer` |
| Owner | `ngocbh` |
| Repository name | `agent_compose` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

**To cut a release:**

1. Bump `version` in `pyproject.toml`, commit, and push to `main`.
2. Tag and create a GitHub Release (e.g. `v0.0.2`) — the `publish` job builds,
   tests, and uploads to PyPI.

**Manual publish** (fallback, needs an account-scoped API token):

```console
pip install build twine
python -m build            # wheel + sdist into dist/
twine upload dist/*        # TWINE_USERNAME=__token__  TWINE_PASSWORD=pypi-...
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
