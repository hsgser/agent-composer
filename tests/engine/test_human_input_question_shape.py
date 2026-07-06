from agent_composer.nodes.human_input.questions import question_list_shape
from agent_composer.state.segments import ValueKind
from agent_composer.nodes.agent.structured import shape_to_schema


def test_question_list_shape_structure():
    sh = question_list_shape()
    assert sh.kind == ValueKind.LIST_OBJECT
    q = sh.element
    assert q.kind == ValueKind.OBJECT
    assert {"question", "header", "options", "multi_select"} <= set(q.fields)
    assert {"question", "header"} <= q.required
    opt = q.fields["options"]
    assert opt.kind == ValueKind.LIST_OBJECT
    assert {"label", "description"} <= set(opt.element.fields)


def test_question_shape_drives_structured_schema():
    # the synth compose-agent generates against this; it must build a pydantic model
    model = shape_to_schema(question_list_shape())
    assert model is not None  # list -> ListWrapper(items=List[Record])
