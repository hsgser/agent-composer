"""`tool_calling` mode emits the declared `output:` shape on its final answer turn."""

import agent_composer.llm_clients as llm_clients_mod
import agent_composer.tools as tools_mod
from agent_composer.llm_clients import LLMConfig
from agent_composer.nodes.agent import AgentNode
from agent_composer.state.pool import TypedVariablePool
from agent_composer.state.segments import Shape, ValueKind

from langchain_core.messages import AIMessage


class _FakeTool:
    def __init__(self, fn):
        self._fn = fn
        self.seen = []

    def invoke(self, args):
        self.seen.append(args)
        return self._fn(args)


def _ai_tool_call(name, args, call_id="1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


class _StructuredFinalChat:
    """A tool-calling model: runs one tool turn, then its final (no-tool) answer is produced
    structurally via with_structured_output for the declared record shape."""

    def __init__(self, schema_value):
        self._value = schema_value
        self._replies = [_ai_tool_call("value", {"topic": "ACME"}), AIMessage(content="done")]
        self.structured_called_with = None

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self._replies.pop(0)

    def with_structured_output(self, schema):
        self.structured_called_with = schema
        value = self._value

        class _Bound:
            def invoke(self, messages):
                return schema.model_validate(value)

        return _Bound()


def _run_node(node, pool=None):
    from agent_composer.runtime.eval_node import eval_node

    return list(eval_node(node, None, pool or TypedVariablePool()))[-1]


def test_tool_calling_final_answer_is_structured(monkeypatch):
    tool = _FakeTool(lambda a: f"px:{a['topic']}")
    monkeypatch.setitem(tools_mod.TOOL_REGISTRY, "value", tool)
    chat = _StructuredFinalChat({"name": "ACME", "score": 9})
    monkeypatch.setattr(llm_clients_mod, "model_from_config", lambda cfg: chat)

    node = AgentNode("n", prompt="hi", tools=["value"], llm_config=LLMConfig(), mode="tool_calling")
    node.output_shape = Shape(
        seg_type=ValueKind.OBJECT,
        fields={
            "name": Shape.scalar(ValueKind.STRING),
            "score": Shape.scalar(ValueKind.INTEGER),
        },
        required=frozenset({"name", "score"}),
    )
    out = _run_node(node).output
    assert out == {"name": "ACME", "score": 9}  # a plain dict, structured final answer
    assert chat.structured_called_with is not None  # the structured emit turn ran
    assert tool.seen == [{"topic": "ACME"}]  # the mid-loop tool still ran


def test_tool_calling_text_answer_unchanged(monkeypatch):
    chat = _StructuredFinalChat({})
    chat._replies = [AIMessage(content="plain answer")]
    monkeypatch.setattr(llm_clients_mod, "model_from_config", lambda cfg: chat)
    node = AgentNode("n", prompt="hi", llm_config=LLMConfig(), mode="tool_calling")
    # no output_shape declared -> text passthrough, no structured emit turn
    assert _run_node(node).output == "plain answer"
    assert chat.structured_called_with is None
