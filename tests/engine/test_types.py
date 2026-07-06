"""Unit tests for the authoring Type grammar + registry resolver."""

import pytest

from agent_composer.state.types import ListType, RefType, ScalarType, parse_type


def test_parse_scalars():
    assert parse_type("str") == ScalarType("str")
    assert parse_type("int") == ScalarType("int")
    assert parse_type("float") == ScalarType("float")
    assert parse_type("date") == ScalarType("date")
    assert parse_type("datetime") == ScalarType("datetime")
    assert parse_type("bool") == ScalarType("bool")
    assert parse_type("object") == ScalarType("object")


def test_parse_lists_and_topics():
    assert parse_type("List[float]") == ListType(ScalarType("float"))
    assert parse_type("list[str]") == ListType(ScalarType("str"))
    assert parse_type("topics") == ListType(ScalarType("str"))
    assert parse_type("List[Rating]") == ListType(RefType("Rating"))


def test_parse_ref():
    assert parse_type("Rating") == RefType("Rating")
    assert parse_type("Action") == RefType("Action")


def test_parse_optional():
    from agent_composer.state.types import OptionalType

    assert parse_type("Optional[str]") == OptionalType(ScalarType("str"))
    assert parse_type("Optional[Rating]") == OptionalType(RefType("Rating"))


def test_resolve_optional_is_nullable():
    from agent_composer.state.types import OptionalType, resolve_shape

    sh = resolve_shape(OptionalType(ScalarType("str")), {})
    assert sh.seg_type == ValueKind.STRING and sh.nullable is True


def test_record_optional_field_excluded_from_required():
    reg = {"Sig": RecordDef(fields={"score": "float", "note": "Optional[str]"})}
    sh = shape_for("Sig", reg)
    assert sh.required == frozenset({"score"})  # note is Optional -> not required
    assert sh.fields["note"].nullable is True
    assert sh.fields["score"].nullable is False


# --- ast type-expr parser (Python + engine names) ----------------- #


def test_parse_python_scalar_names():
    assert parse_type("str") == ScalarType("str")
    assert parse_type("int") == ScalarType("int")
    assert parse_type("float") == ScalarType("float")
    assert parse_type("bool") == ScalarType("bool")
    assert parse_type("Any") == ScalarType("object")
    assert parse_type("date") == ScalarType("date")
    assert parse_type("datetime") == ScalarType("datetime")


def test_parse_python_generics():
    from agent_composer.state.types import OptionalType

    assert parse_type("list[str]") == ListType(ScalarType("str"))
    assert parse_type("List[int]") == ListType(ScalarType("int"))
    assert parse_type("Optional[date]") == OptionalType(ScalarType("date"))
    assert parse_type("list[Rating]") == ListType(RefType("Rating"))


def test_parse_literal_quoted_and_unquoted():
    from agent_composer.state.types import EnumType

    assert parse_type("Literal[pro, con, mixed]") == EnumType(("pro", "con", "mixed"))
    assert parse_type('Literal["pro", "con"]') == EnumType(("pro", "con"))
    assert parse_type("Literal[defer]") == EnumType(("defer",))  # single member


def test_legacy_engine_names_no_longer_resolve():
    # type unification: the OLD engine vocabulary is gone. A bare `string`/`integer`/
    # `number`/`boolean` is no longer a scalar — it parses as an unknown registry RefType
    # and RAISES TypeCheckError on resolution.
    from agent_composer.state.segments import TypeCheckError as _SE

    for legacy in ("string", "integer", "number", "boolean"):
        assert parse_type(legacy) == RefType(legacy)
        with pytest.raises(_SE):
            shape_for(legacy, {})
    with pytest.raises(_SE):
        shape_for("list[string]", {})
    # topics stays a domain alias -> list[str]
    assert parse_type("topics") == ListType(ScalarType("str"))


def test_parse_union_rejected():
    from agent_composer.state.segments import TypeCheckError as _SE

    with pytest.raises(_SE) as ei:
        parse_type("Union[int, str]")
    assert "discriminated record" in str(ei.value) or "case" in str(ei.value)


def test_parse_malformed_raises():
    from agent_composer.state.segments import TypeCheckError as _SE

    with pytest.raises(_SE):
        parse_type("list[")
    with pytest.raises(_SE):
        parse_type("123")  # a number literal is not a type


def test_is_shadow_guard():
    from agent_composer.state.types import _is_shadow

    assert _is_shadow("str") and _is_shadow("int") and _is_shadow("Optional")
    assert _is_shadow("Literal") and _is_shadow("Any") and _is_shadow("list")
    assert not _is_shadow("Rating") and not _is_shadow("Topic")
    assert not _is_shadow("string")  # legacy engine name is no longer a scalar keyword


# --- registry + resolve_shape ----------------------------------------------- #

from agent_composer.state.segments import (  # noqa: E402
    ListObjectValue,
    ObjectValue,
    TypeCheckError,
    ValueKind,
    build_value_as,
)
from agent_composer.state.types import (  # noqa: E402
    RecordDef,
    VariantDef,
    resolve_shape,  # noqa: F401
    shape_for,
)

REG = {
    "Rating": RecordDef(fields={"value": "float", "confidence": "float"}),
    "Action": VariantDef(tags=("Approve", "Reject", "Defer")),
    "Prices": RecordDef(fields={"closes": "List[float]", "last": "float"}),
}


def test_resolve_scalar_and_list():
    assert shape_for("str", REG).seg_type == ValueKind.STRING
    assert shape_for("date", REG).seg_type == ValueKind.DATE
    assert shape_for("datetime", REG).seg_type == ValueKind.DATETIME
    assert shape_for("List[float]", REG).seg_type == ValueKind.LIST_NUMBER


def test_resolve_inline_literal_is_enum():
    from agent_composer.state.types import EnumType, resolve_shape

    sh = resolve_shape(EnumType(("pro", "con")), {})
    assert sh.seg_type == ValueKind.STRING and sh.tags == frozenset({"pro", "con"})


def test_resolve_dict_is_m11_placeholder():
    # dict[K,V] parses + resolves to a lenient object placeholder (full typing is deferred)
    sh = shape_for("dict[str, int]", REG)
    assert sh.seg_type == ValueKind.OBJECT


def test_resolve_record_and_variant():
    s = shape_for("Rating", REG)
    assert s.seg_type == ValueKind.OBJECT and set(s.required) == {"value", "confidence"}
    a = shape_for("Action", REG)
    assert a.seg_type == ValueKind.STRING and a.tags == frozenset({"Approve", "Reject", "Defer"})


def test_resolve_list_of_record():
    s = shape_for("List[Rating]", REG)
    assert s.seg_type == ValueKind.LIST_OBJECT and s.element.fields is not None


def test_unknown_type_raises():
    with pytest.raises(TypeCheckError):
        shape_for("Nope", REG)


def test_recursive_record_rejected():
    bad = {"Node": RecordDef(fields={"child": "Node"})}
    with pytest.raises(TypeCheckError):
        shape_for("Node", bad)


def test_end_to_end_write_boundary():
    assert build_value_as(shape_for("Action", REG), "Approve").value == "Approve"
    with pytest.raises(TypeCheckError):
        build_value_as(shape_for("Action", REG), "approve")
    assert isinstance(
        build_value_as(shape_for("Rating", REG), {"value": 0.8, "confidence": 0.9}),
        ObjectValue,
    )
    assert isinstance(
        build_value_as(shape_for("List[Rating]", REG), [{"value": 0.1, "confidence": 0.2}]),
        ListObjectValue,
    )
    assert isinstance(
        build_value_as(shape_for("Prices", REG), {"closes": [1.0, 2.0], "last": 2.0}),
        ObjectValue,
    )


def test_public_exports():
    import agent_composer.state as state

    for name in (
        "Shape",
        "DateValue",
        "parse_type",
        "resolve_shape",
        "shape_for",
        "RecordDef",
        "VariantDef",
    ):
        assert hasattr(state, name), f"missing export: {name}"
