"""`AgentNode._ctx` threads the node's declared `output_shape` into the mode context."""

import agent_composer.llm_clients as llm_clients_mod
from agent_composer.nodes.agent.node import AgentNode
from agent_composer.runtime.eval_node import _default_llm
from agent_composer.state.segments import Type, ValueKind


def test_ctx_carries_output_shape(monkeypatch):
    # monkeypatch model_from_config so _build_model does not construct a real client.
    monkeypatch.setattr(llm_clients_mod, "model_from_config", lambda cfg: object())
    n = AgentNode("a", prompt="hi")
    n.output_shape = Type.scalar(ValueKind.INTEGER)
    ctx = n._ctx(prompt="hi", llm=_default_llm)
    assert ctx.output_shape == n.output_shape
