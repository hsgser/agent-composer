"""Parser sub-line maps: locate a node input binding / field / assert / input decl by line.

These power precise runtime-error framing — the CLI resolves a `SourceSpan` to a 1-based
source line via these maps. All four mirror `node_lines`/`section_lines`: best-effort over
`yaml.compose`, returning `{}` when the document can't be composed.
"""

from pathlib import Path

from agent_composer.compose.parser import (
    assert_lines,
    def_node_field_lines,
    def_node_lines,
    input_decl_lines,
    node_field_lines,
    node_input_lines,
)

_ERRORS = Path(__file__).resolve().parents[1] / "seeds" / "errors"
_SEEDS = Path(__file__).resolve().parents[1] / "seeds"


def test_node_input_lines_locates_binding():
    text = (_ERRORS / "e07-required-missing.yaml").read_text()
    m = node_input_lines(text)
    assert m["report"]["as_of"] == 23   # the :? binding
    assert m["report"]["topic"] == 22


def test_node_field_lines_locates_kind():
    text = (_ERRORS / "e07-required-missing.yaml").read_text()
    m = node_field_lines(text)
    assert m["report"]["kind"] == 20    # `kind: agent` under report:19


def test_node_field_lines_aliases_legacy_outputs():
    # The legacy plural `outputs:` spelling is aliased to the canonical `output` key so the
    # `field` locator (which always asks for `output`) resolves regardless of spelling.
    text = (
        "id: f\nname: f\nnodes:\n"
        "  calc:\n    kind: code\n    outputs: int\n    code: m:fn\n"
        "output: ${calc.output}\n"
    )
    m = node_field_lines(text)
    assert m["calc"]["output"] == 6     # the `outputs: int` line
    assert m["calc"]["outputs"] == 6    # original spelling still present


def test_assert_lines_flow_level_keyed_by_node_none():
    text = (_ERRORS / "e18-false-boundary-assert.yaml").read_text()
    m = assert_lines(text)
    assert m[(None, "input.window > 0")] == 15


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


def test_def_node_lines_maps_inner_nodes_to_absolute_lines():
    # A def's inner node ids live at absolute lines in the PARENT file; the map keys by
    # def name, then by inner node id.
    text = (_SEEDS / "25-nested-suspension.yaml").read_text()
    m = def_node_lines(text)
    assert m["review"]["approve"] == 26   # `approve:` under defs:review:nodes:
    assert m["review"]["record"] == 32    # `record:` under the same


def test_def_node_field_lines_locates_inner_output():
    text = (_SEEDS / "25-nested-suspension.yaml").read_text()
    m = def_node_field_lines(text)
    assert m["review"]["approve"]["output"] == 31   # the `output: Approval` line


def test_def_node_lines_empty_for_no_defs_file():
    text = (_ERRORS / "e07-required-missing.yaml").read_text()
    assert def_node_lines(text) == {}
    assert def_node_field_lines(text) == {}


def test_def_node_lines_omits_compact_single_node_def():
    # A def in compact form (top-level `kind:`, no `nodes:`) has its sole node
    # synthesized in-memory — no authored inner line — so it is absent from the map.
    text = (
        "id: f\nname: f\ninput:\n  x: str\n"
        "defs:\n"
        "  greet:\n    input:\n      x: str\n    kind: agent\n    output: str\n"
        "    prompt: \"hi ${x}\"\n"
        "nodes:\n  g:\n    kind: call\n    call: greet\n    input: { x: ${input.x} }\n"
        "output: ${g.output}\n"
    )
    assert def_node_lines(text) == {}


def test_def_maps_degrade_to_empty_on_uncomposable():
    bad = "::: not yaml :::\n\t- broken"
    assert def_node_lines(bad) == {}
    assert def_node_field_lines(bad) == {}
