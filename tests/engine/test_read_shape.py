"""Unit tests for read_shape — the Compose D2 reader (YAML -> one Shape)."""

import pytest

from agent_composer.state.segments import ValueKind
from agent_composer.state.types import read_typedefs
from agent_composer.compose import LoadError
from agent_composer.compose.shapes import read_shape


def test_scalar():
    sh = read_shape("float", {})
    assert sh.seg_type == ValueKind.NUMBER
    assert sh.fields is None and sh.element is None


def test_list():
    sh = read_shape("list[str]", {})
    assert sh.seg_type == ValueKind.LIST_STRING
    assert sh.element is not None and sh.element.seg_type == ValueKind.STRING


def test_flat_map():
    sh = read_shape({"rating": "float", "rationale": "str"}, {})
    assert sh.seg_type == ValueKind.OBJECT
    assert sh.fields["rating"].seg_type == ValueKind.NUMBER
    assert sh.fields["rationale"].seg_type == ValueKind.STRING
    assert sh.required == frozenset({"rating", "rationale"})


def test_nested_map():
    sh = read_shape({"summary": {"count": "int", "meta": {"as_of": "date"}}}, {})
    assert sh.seg_type == ValueKind.OBJECT
    summary = sh.fields["summary"]
    assert summary.seg_type == ValueKind.OBJECT
    assert summary.fields["count"].seg_type == ValueKind.INTEGER
    meta = summary.fields["meta"]
    assert meta.seg_type == ValueKind.OBJECT
    assert meta.fields["as_of"].seg_type == ValueKind.DATE


def test_list_of_single_key_maps():
    listed = read_shape([{"decision": "str"}, {"why": "str"}], {})
    flat = read_shape({"decision": "str", "why": "str"}, {})
    assert listed == flat
    assert listed.fields.keys() == {"decision", "why"}


def test_optional_field_excluded_from_required():
    sh = read_shape({"score": "float", "note": "Optional[str]"}, {})
    assert sh.fields["note"].nullable is True
    assert sh.fields["score"].nullable is False
    assert sh.required == frozenset({"score"})


def test_registry_name():
    registry = read_typedefs({"Rating": {"category": "str", "score": "float"}})
    sh = read_shape("Rating", registry)
    assert sh.seg_type == ValueKind.OBJECT
    assert sh.fields["category"].seg_type == ValueKind.STRING
    assert sh.fields["score"].seg_type == ValueKind.NUMBER


def test_malformed_leaf_raises():
    with pytest.raises(LoadError) as exc:
        read_shape("list[", {})
    assert "list[" in str(exc.value)
