"""Authoring Type grammar → runtime Shape.

Parses an `IOField.type` / `types:` reference string into a `Type` AST, and
resolves a `Type` against the per-flow type registry into a leaf `Shape`
(`agent_composer.state.segments`). This is the bridge from the authoring contract
to the typed value system.

Imports `segments` (peer leaf); imported by compile/validation + the runtime
write boundary in later slices.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from typing import Union

from agent_composer.state.segments import (
    TypeCheckError,
    ValueKind,
    Shape,
)


# ---------- Type AST ----------


@dataclass(frozen=True)
class ScalarType:
    name: str  # a scalar keyword (see _SCALAR_KEYWORDS)


@dataclass(frozen=True)
class ListType:
    element: "Type"


@dataclass(frozen=True)
class RefType:
    name: str  # a registry name (record or variant)


@dataclass(frozen=True)
class OptionalType:
    inner: "Type"  # Optional[X] — the resolved Shape is X's, marked nullable


@dataclass(frozen=True)
class EnumType:
    tags: tuple  # an inline Literal[...] enum (a tag-only variant)


@dataclass(frozen=True)
class DictPlaceholderType:
    # dict[K, V] — full key/value typing is deferred; resolves to a lenient object.
    pass


Type = Union[ScalarType, ListType, RefType, OptionalType, EnumType, DictPlaceholderType]

_SCALAR_KEYWORDS = {
    "str", "int", "float", "bool", "date", "datetime", "object", "file", "None", "none",
}

# Names a typedef must not shadow (typing constructors + the generic builtins).
_TYPING_CONSTRUCTORS = {"Optional", "Literal", "Union", "List", "Dict", "list", "dict", "Any"}


def parse_type(s: str) -> Type:
    """Parse a type reference string into a `Type` AST via Python `ast` (eval mode).

    Accepts the one Python-surface vocabulary (`str`/`int`/`float`/`bool`/`Any`/`date`/
    `datetime`/`object`/`file`/`None`, `list[X]`/`Optional[X]`/`dict[K,V]`/`Literal[...]`,
    `topics`). A bare name that is neither a scalar nor a constructor is a registry
    reference. The legacy engine spelling (`string`/`integer`/`number`/`boolean`) no
    longer parses (it resolves as an unknown registry RefType).
    """
    s = s.strip()
    try:
        node = ast.parse(s, mode="eval").body
    except SyntaxError as exc:
        raise TypeCheckError(f"malformed type expression {s!r}: {exc}") from exc
    return _type_from_ast(node, s)


def _type_from_ast(node: "ast.expr", src: str) -> Type:
    # `None` arrives from `ast` as a constant, not a Name — it never reaches the Name arm.
    if isinstance(node, ast.Constant) and node.value is None:
        return ScalarType("None")
    if isinstance(node, ast.Name):
        nm = node.id
        if nm in ("Any", "any"):
            return ScalarType("object")
        if nm == "topics":
            return ListType(ScalarType("str"))  # domain alias (to be retired)
        if nm == "none":
            return ScalarType("None")  # tolerate the historical lowercase token
        if nm in _SCALAR_KEYWORDS:
            return ScalarType(nm)
        return RefType(nm)
    if isinstance(node, ast.Subscript):
        if not isinstance(node.value, ast.Name):
            raise TypeCheckError(f"malformed type expression {src!r}")
        ctor = node.value.id
        sl = node.slice
        if ctor in ("list", "List"):
            return ListType(_type_from_ast(sl, src))
        if ctor == "Optional":
            return OptionalType(_type_from_ast(sl, src))
        if ctor == "Literal":
            return EnumType(_literal_tags(sl, src))
        if ctor in ("dict", "Dict"):
            return DictPlaceholderType()
        if ctor == "Union":
            raise TypeCheckError(
                f"Union types are not supported ({src!r}); model a tagged variant as a "
                f"discriminated record {{tag: Literal[...], ...}} routed by `case ... on tag`"
            )
        raise TypeCheckError(f"unknown type constructor {ctor!r} in {src!r}")
    raise TypeCheckError(f"malformed type expression {src!r}")


def _literal_tags(sl: "ast.expr", src: str) -> tuple:
    elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
    tags = []
    for e in elts:
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            tags.append(e.value)  # quoted member: Literal["red"]
        elif isinstance(e, ast.Name):
            tags.append(e.id)  # unquoted member: Literal[red]
        else:
            raise TypeCheckError(f"invalid Literal member in {src!r} (use names or quoted strings)")
    return tuple(tags)


def _is_shadow(name: str) -> bool:
    """A typedef name must not shadow a scalar keyword or a typing constructor."""
    return name in _SCALAR_KEYWORDS or name.lower() in _SCALAR_KEYWORDS or name in _TYPING_CONSTRUCTORS


# ---------- Registry + resolution ----------


@dataclass(frozen=True)
class RecordDef:
    fields: dict[str, str]  # field name -> type reference string


@dataclass(frozen=True)
class VariantDef:
    tags: tuple[str, ...]


@dataclass(frozen=True)
class AliasDef:
    target: "Type"  # a transparent type abbreviation; resolved transitively (cycle-guarded)


TypeRegistry = dict[str, Union[RecordDef, VariantDef, AliasDef]]

_SCALAR_SEG = {
    "str": ValueKind.STRING,
    "int": ValueKind.INTEGER,
    "float": ValueKind.NUMBER,
    "bool": ValueKind.BOOLEAN,
    "date": ValueKind.DATE,
    "datetime": ValueKind.DATETIME,
    "object": ValueKind.OBJECT,
    "None": ValueKind.NONE,
    "file": ValueKind.FILE,
}

_LIST_SEG_FOR_ELEMENT = {
    ValueKind.STRING: ValueKind.LIST_STRING,
    ValueKind.INTEGER: ValueKind.LIST_INTEGER,
    ValueKind.NUMBER: ValueKind.LIST_NUMBER,
    ValueKind.BOOLEAN: ValueKind.LIST_BOOLEAN,
    ValueKind.OBJECT: ValueKind.LIST_OBJECT,
}


def resolve_shape(t: Type, registry: TypeRegistry, _seen: frozenset[str] = frozenset()) -> Shape:
    """Resolve a `Type` against the registry into a runtime `Shape`."""
    if isinstance(t, ScalarType):
        return Shape.scalar(_SCALAR_SEG[t.name])

    if isinstance(t, OptionalType):
        return replace(resolve_shape(t.inner, registry, _seen), nullable=True)

    if isinstance(t, EnumType):
        return Shape(seg_type=ValueKind.STRING, tags=frozenset(t.tags))

    if isinstance(t, DictPlaceholderType):
        return Shape.scalar(ValueKind.OBJECT)  # lenient (no key/value typing yet)

    if isinstance(t, ListType):
        elem = resolve_shape(t.element, registry, _seen)
        if elem.tags is not None:  # list of variant -> list of strings
            list_seg = ValueKind.LIST_STRING
        elif elem.fields is not None:  # list of record -> list of objects
            list_seg = ValueKind.LIST_OBJECT
        else:
            list_seg = _LIST_SEG_FOR_ELEMENT.get(elem.seg_type, ValueKind.LIST_ANY)
        return Shape(seg_type=list_seg, element=elem)

    # RefType
    name = t.name
    if name in _seen:
        raise TypeCheckError(f"recursive type reference: {name!r}")
    defn = registry.get(name)
    if defn is None:
        raise TypeCheckError(f"unknown type {name!r}")
    if isinstance(defn, AliasDef):
        return resolve_shape(defn.target, registry, _seen | {name})  # transitive (cycle-guarded)
    if isinstance(defn, VariantDef):
        return Shape(seg_type=ValueKind.STRING, tags=frozenset(defn.tags))
    fields = {
        f: resolve_shape(parse_type(ts), registry, _seen | {name})
        for f, ts in defn.fields.items()
    }
    # an Optional[X] field (its resolved Shape is nullable) is NOT required.
    required = frozenset(f for f, sh in fields.items() if not sh.nullable)
    return Shape(seg_type=ValueKind.OBJECT, fields=fields, required=required)


def shape_for(type_str: str, registry: TypeRegistry) -> Shape:
    """Convenience: parse a type string and resolve it against the registry."""
    return resolve_shape(parse_type(type_str), registry)


# ---------- typedefs: registry builder ----------


def _refs_in_type(t: Type) -> set:
    """The registry names a parsed Type references (for the eager alias-cycle check)."""
    if isinstance(t, RefType):
        return {t.name}
    if isinstance(t, ListType):
        return _refs_in_type(t.element)
    if isinstance(t, OptionalType):
        return _refs_in_type(t.inner)
    return set()  # scalars / enums / dict-placeholder reference nothing


def _check_alias_cycles(registry: TypeRegistry) -> None:
    """Reject an alias->alias cycle eagerly, located at the typedefs block (records are
    caught lazily by resolve_shape's `_seen`)."""
    def visit(name: str, path: frozenset) -> None:
        defn = registry.get(name)
        if not isinstance(defn, AliasDef):
            return
        for ref in _refs_in_type(defn.target):
            if ref in path:
                raise TypeCheckError(f"alias cycle through {ref!r} in typedefs")
            if ref in registry:
                visit(ref, path | {ref})

    for name, defn in registry.items():
        if isinstance(defn, AliasDef):
            visit(name, frozenset({name}))


def read_typedefs(raw: dict) -> TypeRegistry:
    """Build a `TypeRegistry` from a raw `typedefs:` mapping (Python-typing surface).

    A mapping value is a RECORD (`RecordDef`; field type-strings kept raw, resolved
    lazily). A `Literal[...]` string is an ENUM (`VariantDef`, tag-only). Any other
    string is an ALIAS (`AliasDef`). A **sequence** is the dropped tagged-union spelling
    and is rejected: an all-bare-tags sequence must be a `Literal[...]` enum
    (the e05 case); a payload sequence must be a discriminated record routed by
    `case ... on tag`. Names must be PascalCase and must not shadow a scalar/typing
    constructor. Alias cycles are rejected eagerly.
    """
    registry: TypeRegistry = {}
    for name, value in (raw or {}).items():
        if not (isinstance(name, str) and name.isidentifier() and name[0].isupper()):
            raise TypeCheckError(f"typedef name {name!r} must be PascalCase (an uppercase identifier)")
        if _is_shadow(name):
            raise TypeCheckError(
                f"typedef name {name!r} shadows a builtin scalar or typing constructor"
            )
        if isinstance(value, dict):
            for fname, ftype in value.items():
                if not isinstance(ftype, str):
                    raise TypeCheckError(
                        f"typedef {name!r} field {fname!r}: type must be a string expression "
                        f"(got {type(ftype).__name__})"
                    )
            registry[name] = RecordDef(fields={str(k): v for k, v in value.items()})
        elif isinstance(value, str):
            parsed = parse_type(value)
            if isinstance(parsed, EnumType):
                registry[name] = VariantDef(tags=parsed.tags)
            else:
                registry[name] = AliasDef(target=parsed)
        elif isinstance(value, (list, tuple)):
            if all(isinstance(m, str) for m in value):
                members = ", ".join(str(m) for m in value)
                raise TypeCheckError(
                    f"typedef {name!r}: a tag-only union must be a Literal[...] enum, not a "
                    f"sequence (e.g. {name}: Literal[{members}])"
                )
            raise TypeCheckError(
                f"typedef {name!r}: a tagged/payload union sequence is not supported; model it "
                f"as a discriminated record {{tag: Literal[...], ...}} routed by `case ... on tag`"
            )
        else:
            raise TypeCheckError(
                f"typedef {name!r}: unsupported definition of type {type(value).__name__}"
            )
    _check_alias_cycles(registry)
    return registry
