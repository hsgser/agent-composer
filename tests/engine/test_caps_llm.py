"""P7: LLM client injected via caps["llm"], gated on the needs_llm trait."""
from agent_composer.nodes.base import Node
from agent_composer.nodes.agent.node import AgentNode


def test_base_node_does_not_need_llm():
    assert Node.needs_llm is False


def test_agent_node_needs_llm():
    assert AgentNode.needs_llm is True


from agent_composer.nodes.base import NodeKind, Output
from agent_composer.state.pool import TypedVariablePool
from agent_composer.runtime.eval_node import eval_node, _default_llm


def test_eval_node_passes_llm_cap_to_needs_llm_node():
    seen = {}

    class Fake(AgentNode):
        def run(self, inputs, **caps):
            seen["llm"] = caps.get("llm")
            return Output(value="ok")

    node = Fake("a", prompt="hi")
    sentinel = lambda cfg: object()
    list(eval_node(node, None, TypedVariablePool(), llm=sentinel))
    assert seen["llm"] is sentinel


def test_eval_node_omits_llm_cap_for_plain_node():
    # A non-needs_llm node must not receive an llm cap.
    seen = {}

    class Plain(Node):
        kind = NodeKind.CODE  # any concrete kind; needs_llm stays False (base default)

        def run(self, inputs, **caps):
            seen["caps"] = dict(caps)
            return Output(value="ok")

    node = Plain("p")
    list(eval_node(node, None, TypedVariablePool(), llm=lambda cfg: object()))
    assert "llm" not in seen["caps"]

