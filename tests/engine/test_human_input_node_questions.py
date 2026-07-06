"""HUMAN_INPUT gate: run() resolves/validates/renders questions and pauses.

Covers the three question-bearing forms (literal baked-in, runtime input-ref,
bad fed shape) plus the legacy prompt-only path, asserting the `Pause` the node
returns and the `HumanInputRequired` reason it carries.
"""

import pytest

from agent_composer.nodes.base import Pause
from agent_composer.nodes.human_input.node import HumanInputNode
from agent_composer.nodes.human_input.questions import QuestionSpecError
from agent_composer.suspension.pause import HumanInputRequired


def test_literal_questions_render_and_pause():
    node = HumanInputNode(
        "ask",
        questions=[
            {"question": "Pick ${x}", "header": "H", "options": [{"label": "A"}]}
        ],
    )
    result = node.run({"x": "framework"})

    assert isinstance(result, Pause)
    reason = result.reason
    assert isinstance(reason, HumanInputRequired)
    assert len(reason.questions) == 1
    q = reason.questions[0]
    assert q["question"] == "Pick framework"
    assert q["header"] == "H"


def test_questions_input_ref_resolves():
    node = HumanInputNode("ask", questions_input="qs")
    result = node.run(
        {"qs": [{"question": "Q", "header": "H", "options": [{"label": "A"}]}]}
    )

    assert isinstance(result, Pause)
    reason = result.reason
    assert len(reason.questions) == 1
    assert reason.questions[0]["question"] == "Q"


def test_bad_fed_type_raises():
    node = HumanInputNode("ask", questions_input="qs")
    with pytest.raises(QuestionSpecError):
        node.run({"qs": []})  # empty list -> parse_questions rejects


def test_legacy_prompt_only_unchanged():
    node = HumanInputNode("ask", prompt="Approve ${x}?")
    result = node.run({"x": "deploy"})

    assert isinstance(result, Pause)
    reason = result.reason
    assert reason.prompt == "Approve deploy?"
    assert reason.questions == []
