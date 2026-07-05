"""P7: LLM client injected via caps["llm"], gated on the needs_llm trait."""
from agent_composer.nodes.base import Node
from agent_composer.nodes.agent.node import AgentNode


def test_base_node_does_not_need_llm():
    assert Node.needs_llm is False


def test_agent_node_needs_llm():
    assert AgentNode.needs_llm is True
