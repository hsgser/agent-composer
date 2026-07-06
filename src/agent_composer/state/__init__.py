"""Typed runtime state: the segment value system + the variable pool."""

from agent_composer.state.pool import TypedVariablePool
from agent_composer.state.segments import (
    AnyValue,
    DateValue,
    DateTimeValue,
    FileRef,
    TypedValue,
    TypeCheckError,
    ValueKind,
    Shape,
    build_value,
    build_value_as,
)
from agent_composer.state.types import (
    ListType,
    RecordDef,
    RefType,
    ScalarType,
    Type,
    TypeRegistry,
    VariantDef,
    parse_type,
    resolve_shape,
    shape_for,
)

__all__ = [
    "AnyValue",
    "DateValue",
    "DateTimeValue",
    "FileRef",
    "ListType",
    "RecordDef",
    "RefType",
    "ScalarType",
    "TypedValue",
    "TypeCheckError",
    "ValueKind",
    "Shape",
    "Type",
    "TypeRegistry",
    "TypedVariablePool",
    "VariantDef",
    "build_value",
    "build_value_as",
    "parse_type",
    "resolve_shape",
    "shape_for",
]
