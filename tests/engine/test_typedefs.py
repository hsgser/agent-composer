"""Unit tests for read_typedefs — the `typedefs:` registry builder."""

import pytest

from agent_composer.typesys.values import TypeCheckError, ValueKind
from agent_composer.typesys.types import (
    AliasDef,
    RecordDef,
    RefExpr,
    VariantDef,
    read_typedefs,
    resolve_type,
    type_for,
)


def test_record_enum_alias_build():
    reg = read_typedefs({
        "Topic": "str",
        "Amount": "float",
        "Category": "Literal[pro, con, mixed]",
        "Rating": {"category": "Category", "score": "float", "note": "Optional[str]"},
    })
    assert isinstance(reg["Topic"], AliasDef)
    assert isinstance(reg["Amount"], AliasDef)
    assert isinstance(reg["Category"], VariantDef) and reg["Category"].tags == ("pro", "con", "mixed")
    assert isinstance(reg["Rating"], RecordDef)


def test_alias_composes_alias():
    reg = read_typedefs({"Topic": "str", "Bundle": "list[Topic]"})
    assert type_for("Bundle", reg).kind == ValueKind.LIST_STRING


def test_record_resolves_with_enum_and_optional():
    reg = read_typedefs({
        "Category": "Literal[pro, con]",
        "Rating": {"category": "Category", "score": "float", "note": "Optional[str]"},
    })
    sh = type_for("Rating", reg)
    assert sh.required == frozenset({"category", "score"})  # note (Optional) is not required
    assert sh.fields["category"].tags == frozenset({"pro", "con"})
    assert sh.fields["note"].nullable is True


def test_all_bare_tags_sequence_rejected_e05():
    # e05 mechanism: a tag-only union must be Literal[...], not a sequence.
    with pytest.raises(TypeCheckError) as ei:
        read_typedefs({"Category": ["pro", "con"]})
    assert "Literal" in str(ei.value)


def test_payload_union_sequence_rejected():
    with pytest.raises(TypeCheckError) as ei:
        read_typedefs({"Choice": ["defer", {"approve": {"count": "int"}}]})
    assert "discriminated record" in str(ei.value) or "case" in str(ei.value)


def test_alias_cycle_rejected_eagerly():
    with pytest.raises(TypeCheckError) as ei:
        read_typedefs({"A": "B", "B": "A"})
    assert "cycle" in str(ei.value)


def test_recursive_record_rejected_lazily():
    # read_typedefs keeps record field types raw, so the build is fine; the cycle is
    # caught lazily at resolve via resolve_type's `_seen` guard.
    reg = read_typedefs({"R": {"x": "R"}})
    with pytest.raises(TypeCheckError):
        resolve_type(RefExpr("R"), reg)


def test_shadow_name_rejected():
    with pytest.raises(TypeCheckError):
        read_typedefs({"str": "int"})
    with pytest.raises(TypeCheckError):
        read_typedefs({"Optional": "int"})


def test_non_pascalcase_rejected():
    with pytest.raises(TypeCheckError) as ei:
        read_typedefs({"rating": {"x": "int"}})
    assert "PascalCase" in str(ei.value)


def test_record_field_must_be_string():
    with pytest.raises(TypeCheckError) as ei:
        read_typedefs({"Bad": {"x": {"nested": "int"}}})  # inline nested object not allowed here
    assert "string expression" in str(ei.value)
