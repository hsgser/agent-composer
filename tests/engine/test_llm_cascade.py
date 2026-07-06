"""Cascade resolution tests — the per-field fill-the-gap merge primitive and the
recursive `resolve_llm_cascade` walk over the static call tree."""

from agent_composer.llm_clients.config import merge_llm_config
from agent_composer.compose.loader import load_flow
from agent_composer.compile.llm_cascade import resolve_llm_cascade


def test_merge_specific_wins_per_field():
    specific = {"model": "claude-opus-4-8"}
    base = {"provider": "anthropic", "model": "claude-sonnet-4-5", "temperature": 0.2}
    # specific wins per field; base fills the gaps (provider, temperature)
    assert merge_llm_config(specific, base) == {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "temperature": 0.2,
    }


def test_merge_empty_layers():
    assert merge_llm_config({}, {}) == {}
    assert merge_llm_config({"model": "x"}, {}) == {"model": "x"}
    assert merge_llm_config({}, {"model": "y"}) == {"model": "y"}


def test_merge_does_not_mutate_inputs():
    a, b = {"model": "x"}, {"provider": "p"}
    merge_llm_config(a, b)
    assert a == {"model": "x"} and b == {"provider": "p"}


def test_node_fills_gap_from_flow_then_cli():
    text = """
id: f
name: f
llm_config: {provider: anthropic}
nodes:
  a: {kind: agent, prompt: hi, llm_config: {model: claude-opus-4-8}}
output: ${a.output}
"""
    loaded = load_flow(text)
    resolve_llm_cascade(loaded.compiled, {"temperature": 0.3})
    a = loaded.compiled.nodes["a"]
    # node sets model; flow fills provider; CLI fills temperature
    assert a.llm_config == {
        "model": "claude-opus-4-8", "provider": "anthropic", "temperature": 0.3
    }


def test_inherit_false_node_ignores_all_layers():
    text = """
id: f
name: f
llm_config: {provider: anthropic, temperature: 0.9}
nodes:
  a: {kind: agent, prompt: hi, llm_config: {model: m, inherit: false}}
output: ${a.output}
"""
    loaded = load_flow(text)
    resolve_llm_cascade(loaded.compiled, {"temperature": 0.1})
    assert loaded.compiled.nodes["a"].llm_config == {"model": "m"}


def test_resolution_is_idempotent_from_own():
    # re-resolving with a different CLI layer recomputes from own_llm_config, not the
    # previously-baked effective dict
    text = "id: f\nname: f\nnodes:\n  a: {kind: agent, prompt: hi, llm_config: {model: m}}\noutput: ${a.output}\n"
    loaded = load_flow(text)
    resolve_llm_cascade(loaded.compiled, {"provider": "openai"})
    resolve_llm_cascade(loaded.compiled, {"provider": "google"})
    assert loaded.compiled.nodes["a"].llm_config == {"model": "m", "provider": "google"}


def test_cascade_reaches_nested_def_agents():
    text = """
id: f
name: f
llm_config: {provider: anthropic}
defs:
  sub:
    nodes:
      inner: {kind: agent, prompt: hi}
    output: ${inner.output}
nodes:
  c: {kind: call, call: sub}
output: ${c.output}
"""
    loaded = load_flow(text)
    resolve_llm_cascade(loaded.compiled, {"model": "claude-opus-4-8"})
    call = loaded.compiled.nodes["c"]
    inner = call.child.nodes["inner"]
    # inner sets nothing -> fills from top flow (provider) + CLI (model)
    assert inner.llm_config == {"provider": "anthropic", "model": "claude-opus-4-8"}


def test_nested_re_resolution_recomputes_from_own():
    # the riskier path — resolve_llm_cascade replaces node.child with a fresh deepcopy each
    # call, so re-running over a CALL must still recompute the inner agent from its OWN config
    # (never compound the previously-baked effective dict).
    text = """
id: f
name: f
defs:
  sub:
    nodes:
      inner: {kind: agent, prompt: hi}
    output: ${inner.output}
nodes:
  c: {kind: call, call: sub}
output: ${c.output}
"""
    loaded = load_flow(text)
    resolve_llm_cascade(loaded.compiled, {"provider": "openai", "model": "x"})
    resolve_llm_cascade(loaded.compiled, {"provider": "google"})
    inner = loaded.compiled.nodes["c"].child.nodes["inner"]
    # second run dropped `model` (not in the new CLI layer); did not retain it from run 1
    assert inner.llm_config == {"provider": "google"}


def test_cascade_reaches_loop_body_agents():
    # A LOOP carries a static child body like CALL/MAP; its body agents must inherit the
    # parent flow/CLI layers too (cascade recurses into any node with a static child flow).
    text = """
id: f
name: f
llm_config: {provider: anthropic}
defs:
  bump:
    input:
      n: int
      exited: bool
    nodes:
      think: {kind: agent, prompt: hi}
      step:
        kind: code
        code: tests.engine._compose_codefns:make_report
        input:
          n: ${input.n}
          exited: ${input.exited}
        output:
          n: int
          exited: bool
    output: ${step.output}
nodes:
  chat_loop:
    kind: loop
    call: bump
    input:
      n: 0
      exited: false
    while: not ${exited}
    max: 5
output: ${chat_loop.output}
"""
    loaded = load_flow(text)
    resolve_llm_cascade(loaded.compiled, {"model": "claude-opus-4-8"})
    think = loaded.compiled.nodes["chat_loop"].child.nodes["think"]
    # the body agent sets nothing -> fills from top flow (provider) + CLI (model)
    assert think.llm_config == {"provider": "anthropic", "model": "claude-opus-4-8"}
