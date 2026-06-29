"""Typed specs for human-input questions and a validating parser.

A *question* is one prompt the host shows a person: free-text by default, or a
choice over `options` (single- or multi-select). `parse_questions` turns the raw
YAML/JSON list authored in a flow into validated `QuestionSpec` objects, applying
the engine's authoring constraints (1..4 questions, unique headers).
"""

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_composer.state.segments import Shape, SegmentType


class OptionSpec(BaseModel):
    """One selectable choice within a question.

    Args:
        label (`str`):
            The choice text shown to and returned by the person; required.
        description (`str`, *optional*, defaults to `""`):
            Optional helper text explaining the choice; empty when omitted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    description: str = ""


class QuestionSpec(BaseModel):
    """A single question posed to a person.

    With no `options` the question is free-text; with `options` it is a choice,
    single-select unless `multi_select` is set.

    Args:
        question (`str`):
            The prompt text; required.
        header (`str`):
            A short, unique-within-the-set key identifying this question (used to
            key the collected answer); required.
        options (`list[OptionSpec]`, *optional*, defaults to `[]`):
            The selectable choices; an empty list means the question is free-text.
        multi_select (`bool`, *optional*, defaults to `False`):
            When `True`, the person may pick more than one option; ignored for
            free-text questions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    header: str
    options: list[OptionSpec] = Field(default_factory=list)
    multi_select: bool = False


class QuestionSpecError(ValueError):
    """Raised when a raw question list violates the authoring constraints."""


def parse_questions(raw) -> list[QuestionSpec]:
    """Validate a raw question list and return the parsed `QuestionSpec` objects.

    Enforces the engine's authoring constraints: the input is a list of 1..4
    items, each item is a valid `QuestionSpec`, and every `header` is unique.

    Args:
        raw (`list[dict]`):
            The author-supplied question list (as loaded from YAML/JSON). Each
            item is a mapping shaped like `QuestionSpec`.

    Returns:
        `list[QuestionSpec]`:
            The validated questions, in input order.

    Raises:
        `QuestionSpecError`:
            If `raw` is not a list, has fewer than 1 or more than 4 items, any
            item fails `QuestionSpec` validation, or two items share a `header`.
    """
    if not isinstance(raw, list):
        raise QuestionSpecError(f"questions must be a list, got {type(raw).__name__}")
    if not (1 <= len(raw) <= 4):
        raise QuestionSpecError(f"expected 1..4 questions, got {len(raw)}")

    questions: list[QuestionSpec] = []
    for i, item in enumerate(raw):
        try:
            questions.append(QuestionSpec.model_validate(item))
        except (ValidationError, TypeError) as exc:
            raise QuestionSpecError(f"question[{i}] is invalid: {exc}") from exc

    headers = [q.header for q in questions]
    if len(set(headers)) != len(headers):
        raise QuestionSpecError(f"question headers must be unique, got {headers}")

    return questions


def question_list_shape() -> Shape:
    """Build the typed `Shape` a synthesized compose-agent generates questions against.

    Mirrors `QuestionSpec`/`OptionSpec` in the engine's Shape vocabulary so the
    structured-output model emits a `list` of question records:

        LIST_OBJECT element = OBJECT{
          question: str (required),
          header:   str (required),
          options:  LIST_OBJECT element = OBJECT{ label: str (req), description: str (req) },
          multi_select: bool,
        }

    `description` is kept required on the option record so the model always emits
    it (rather than dropping the helper text).

    Returns:
        `Shape`:
            A `LIST_OBJECT` shape whose `element` is the question record above.
    """
    option_record = Shape(
        seg_type=SegmentType.OBJECT,
        fields={
            "label": Shape.scalar(SegmentType.STRING),
            "description": Shape.scalar(SegmentType.STRING),
        },
        required=frozenset({"label", "description"}),
    )
    question_record = Shape(
        seg_type=SegmentType.OBJECT,
        fields={
            "question": Shape.scalar(SegmentType.STRING),
            "header": Shape.scalar(SegmentType.STRING),
            "options": Shape(seg_type=SegmentType.LIST_OBJECT, element=option_record),
            "multi_select": Shape.scalar(SegmentType.BOOLEAN),
        },
        required=frozenset({"question", "header"}),
    )
    return Shape(seg_type=SegmentType.LIST_OBJECT, element=question_record)
