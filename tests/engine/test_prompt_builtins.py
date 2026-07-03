"""Prompt builtins — `${ fn(${ref}, lit).path }` callable inside an AGENT/HUMAN_INPUT prompt.

Two layers:
- runtime: `render_template_record` evaluates a span as a plain ref OR a `TEMPLATE_FNS`
  builtin call (positional + keyword args, `${...}`-wrapped record refs or literals,
  optional dotted access on the result);
- compile: `prompt_refs` extracts the declared-input refs a prompt reads (so the loader
  can name-check prompt scope) and rejects an unknown builtin / malformed span.

The feature is prompt-only: it mints no graph node or edge (see agent-compose-principles
§4(A)). These tests are Ollama-free — the renderer runs without any model.
"""

import pytest

from agent_composer.compose import LoadError, load_flow
from agent_composer.expr import ExpressionError, prompt_refs, render_template_record
from agent_composer.expr.builtins import register_template_fn

_REC = {"briefs": ["ab", "adf"], "name": "zeta", "sig": {"value": 0.8}}


# --- render: builtin calls --------------------------------------------------- #


def test_render_as_json_positional_ref_and_literal():
    # `${briefs}` -> the typed list; `4` -> the int `indent` (positional).
    out = render_template_record("J=${render_as_json(${briefs}, 4)}", _REC)
    assert out == 'J=[\n    "ab",\n    "adf"\n]'


def test_render_as_json_keyword_form():
    out = render_template_record("J=${render_as_json(value=${briefs}, indent=4)}", _REC)
    assert out == 'J=[\n    "ab",\n    "adf"\n]'


def test_join_with_keyword_sep():
    assert render_template_record("${join(${briefs}, sep=', ')}", _REC) == "ab, adf"


def test_upper_lower_over_a_ref():
    assert render_template_record("${upper(${name})}/${lower('HI')}", _REC) == "ZETA/hi"


def test_dotted_access_on_call_result():
    register_template_fn("_mkrec")(lambda x: {"field": f"F-{x}"})
    assert render_template_record("${_mkrec(${name}).field}", _REC) == "F-zeta"


# --- render: unified-expression spans (new grammar) -------------------------- #


def test_arithmetic_in_a_prompt_span():
    # NEW under the unified engine: a `${}` span is a full expression, so arithmetic
    # over a ref renders its computed value, stringified (the old prompt grammar had no
    # arithmetic — a span was a plain ref or a builtin call only).
    assert render_template_record("total ${a + 1}", {"a": 4}) == "total 5"


def test_dollar_dollar_renders_single_dollar():
    # INTENTIONAL, LOCKED change: the legacy renderer emitted `$$` verbatim; the unified
    # scanner collapses `$$` -> a single `$` everywhere (the universal escape). A prompt
    # containing `$$` now renders one `$`.
    assert render_template_record("cost is $$5", {}) == "cost is $5"


# --- render: error floor ----------------------------------------------------- #


def test_unknown_builtin_raises():
    with pytest.raises(ExpressionError):
        render_template_record("${frobnicate(${name})}", _REC)


def test_bare_word_arg_is_a_ref_option_a():
    # Option A: inside `${}` a BARE word is a REFERENCE (literals must be quoted). So a
    # bare call arg resolves against the record — the old "bare word is neither ref nor
    # literal -> raise" rule is gone. `${...}`-wrapping a ref still works (see below).
    out = render_template_record("${render_as_json(briefs, 4)}", _REC)
    assert out == '[\n    "ab",\n    "adf"\n]'


def test_arg_ref_strict_on_missing():
    with pytest.raises(ExpressionError):
        render_template_record("${render_as_json(${ghost})}", _REC)


def test_dotted_access_miss_raises():
    register_template_fn("_mkrec")(lambda x: {"field": f"F-{x}"})
    with pytest.raises(ExpressionError):
        render_template_record("${_mkrec(${name}).nope}", _REC)


# --- render: plain-ref regression (the pre-builtin behavior is unchanged) ----- #


def test_plain_ref_and_dotted_still_work():
    assert render_template_record("${name} v=${sig.value}", _REC) == "zeta v=0.8"


def test_plain_ref_unknown_or_none_still_raises():
    with pytest.raises(ExpressionError):
        render_template_record("${ghost}", _REC)
    with pytest.raises(ExpressionError):
        render_template_record("${t}", {"t": None})


def test_nested_braces_no_longer_misparsed():
    # the old `_VAR_RE` regex stopped at the first `}` — the brace-aware scanner does not.
    assert render_template_record("[${render_as_json(${briefs})}]", _REC).startswith("[")


# --- prompt_refs (compile-time companion) ------------------------------------ #


def test_prompt_refs_collects_plain_and_arg_refs():
    refs = prompt_refs("a ${name} b ${render_as_json(${briefs}, 4)} c ${join(${sig.value})}")
    assert refs == ["name", "briefs", "sig.value"]


def test_prompt_refs_skips_literals():
    assert prompt_refs("${render_as_json(${briefs}, 4)} ${lower('HI')}") == ["briefs"]


def test_prompt_refs_rejects_unknown_builtin():
    with pytest.raises(ExpressionError):
        prompt_refs("${frobnicate(${name})}")


# --- loader: prompt scope is call-aware -------------------------------------- #

_FLOW = """
id: pb
name: pb
input:
  topic: str
nodes:
  brief:
    kind: agent
    input:
      topic: ${{input.topic}}
    output: str
    prompt: "Render {call}"
output: ${{brief.output}}
"""


def test_loader_accepts_builtin_call_over_declared_input():
    flow = load_flow(_FLOW.format(call="${render_as_json(${topic}, 2)}"))
    assert flow is not None


def test_loader_rejects_undeclared_input_inside_call():
    with pytest.raises(LoadError) as exc:
        load_flow(_FLOW.format(call="${render_as_json(${ghost})}"))
    assert "is not a declared input" in str(exc.value)


def test_loader_rejects_unknown_builtin():
    with pytest.raises(LoadError) as exc:
        load_flow(_FLOW.format(call="${frobnicate(${topic})}"))
    assert "frobnicate" in str(exc.value)
