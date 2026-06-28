"""Parser sub-line maps: locate a node input binding / field / assert / input decl by line.

These power precise runtime-error framing — the CLI resolves a `SourceSpan` to a 1-based
source line via these maps. All four mirror `node_lines`/`section_lines`: best-effort over
`yaml.compose`, returning `{}` when the document can't be composed.
"""

from pathlib import Path

from agent_composer.compose.parser import (
    assert_lines,
    input_decl_lines,
    node_field_lines,
    node_input_lines,
)

_ERRORS = Path(__file__).resolve().parents[1] / "seeds" / "errors"


def test_node_input_lines_locates_binding():
    text = (_ERRORS / "e07-required-missing.yaml").read_text()
    m = node_input_lines(text)
    assert m["report"]["as_of"] == 23   # the :? binding
    assert m["report"]["topic"] == 22


def test_node_field_lines_locates_kind():
    text = (_ERRORS / "e07-required-missing.yaml").read_text()
    m = node_field_lines(text)
    assert m["report"]["kind"] == 20    # `kind: agent` under report:19


def test_assert_lines_flow_level_keyed_by_node_none():
    text = (_ERRORS / "e18-false-boundary-assert.yaml").read_text()
    m = assert_lines(text)
    assert m[(None, "${input.window} > 0")] == 15


def test_input_decl_lines_locates_decl():
    text = (_ERRORS / "e07-required-missing.yaml").read_text()
    m = input_decl_lines(text)
    assert m["topic"] == 15 and m["as_of"] == 16


def test_maps_degrade_to_empty_on_uncomposable():
    bad = "::: not yaml :::\n\t- broken"
    assert node_input_lines(bad) == {}
    assert node_field_lines(bad) == {}
    assert assert_lines(bad) == {}
    assert input_decl_lines(bad) == {}
