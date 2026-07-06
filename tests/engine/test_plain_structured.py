"""`plain` mode — native structured output when the node declares a non-text shape."""

from agent_composer.nodes.agent.modes.common import AgentRunContext
from agent_composer.nodes.agent.modes.plain import plain
from agent_composer.state.segments import Shape, ValueKind


class _StructuredModel:
    def __init__(self):
        self.structured_called_with = None

    def with_structured_output(self, schema):
        self.structured_called_with = schema

        class _Bound:
            def invoke(self, msgs):
                return schema.model_validate({"name": "Ada", "score": 9})

        return _Bound()

    def invoke(self, msgs):
        raise AssertionError("should use with_structured_output for a declared shape")


def test_plain_uses_structured_output_for_record():
    shape = Shape(
        seg_type=ValueKind.OBJECT,
        fields={
            "name": Shape.scalar(ValueKind.STRING),
            "score": Shape.scalar(ValueKind.INTEGER),
        },
        required=frozenset({"name", "score"}),
    )
    model = _StructuredModel()
    ctx = AgentRunContext(node_id="a", prompt="x", model=model, output_shape=shape)
    out = plain(ctx)
    assert out.value == {"name": "Ada", "score": 9}  # a plain dict, not a pydantic obj


class _TextModel:
    def invoke(self, msgs):
        class R:
            content = "hello"

        return R()


def test_plain_bare_str_text_passthrough():
    ctx = AgentRunContext(
        node_id="a", prompt="x", model=_TextModel(),
        output_shape=Shape.scalar(ValueKind.STRING),
    )
    assert plain(ctx).value == "hello"  # unchanged behavior
