"""Authorable `retries:` field — parsed onto the node and threaded into the mode context."""

import agent_composer.llm_clients as llm_clients_mod
from agent_composer.compose.loader import load_flow
from agent_composer.runtime.eval_node import _default_llm


_FLOW = """
id: f
name: f
nodes:
  a:
    kind: agent
    prompt: hi
    retries: 5
    output: int
output: ${a.output}
"""

_FLOW_DEFAULT = """
id: f
name: f
nodes:
  a:
    kind: agent
    prompt: hi
    output: int
output: ${a.output}
"""


def test_retries_field_parsed_and_threaded(monkeypatch):
    monkeypatch.setattr(llm_clients_mod, "model_from_config", lambda cfg: object())
    loaded = load_flow(_FLOW)
    node = loaded.compiled.nodes["a"]
    assert node.retries == 5
    assert node._ctx(prompt="hi", llm=_default_llm).retries == 5


def test_retries_defaults_to_two():
    loaded = load_flow(_FLOW_DEFAULT)
    assert loaded.compiled.nodes["a"].retries == 2
