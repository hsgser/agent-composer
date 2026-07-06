"""build_leaf_node honors AgentDescriptor.output_shape_override.

The adaptive_questions desugar pass synthesizes an AGENT whose structured
output is a code-built `list[Question]` Type (it has no surface type-string).
The override lets that code-built Type win over the type-string-derived shape.
"""

from agent_composer.compose.parser import AgentDescriptor
from agent_composer.compose.build import build_leaf_node
from agent_composer.nodes.human_input.questions import question_list_shape


def test_override_wins_over_outputs_string():
    desc = AgentDescriptor(
        id="g__compose",
        prompt="x",
        inputs={},
        output_shape_override=question_list_shape(),
    )
    node, _ = build_leaf_node(desc, {})
    assert node.output_shape.kind.value == "list[object]"
