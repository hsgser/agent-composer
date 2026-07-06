"""The D2 reader: a Compose `outputs:`/`inputs:` source node -> one runtime `Shape`.

A node here is a leaf type string (`"float"`, `"list[str]"`, a registry name), a
map of field -> node (a record, nested natively), or a list of single-key maps
(D2's list-of-fields style). Resolution of leaf strings and nested records is the
state layer's job (`shape_for` recurses over records/lists/Optional); this reader
is the thin composition that walks the YAML shape and wraps any type error loudly.
"""

from dataclasses import dataclass
from typing import Any, Optional

import yaml

from agent_composer.state import TypeCheckError, Shape, shape_for
from agent_composer.state.segments import ValueKind
from agent_composer.state.types import ScalarType, TypeRegistry, parse_type

from agent_composer.compose.errors import LoadError


def read_shape(node, registry: TypeRegistry) -> Shape:
    """Read a Compose source node into one runtime `Shape` (recursively)."""
    if isinstance(node, str):
        try:
            return shape_for(node, registry)
        except TypeCheckError as exc:
            raise LoadError(f"bad type expression {node!r}: {exc}") from exc

    if isinstance(node, dict):
        fields = {k: read_shape(v, registry) for k, v in node.items()}
        return Shape(
            seg_type=ValueKind.OBJECT,
            fields=fields,
            required=frozenset(k for k, sh in fields.items() if not sh.nullable),
        )

    if isinstance(node, list):
        merged: dict = {}
        for elem in node:
            if not (isinstance(elem, dict) and len(elem) == 1):
                raise LoadError(
                    f"a list of fields must hold single-key maps, got {elem!r}"
                )
            merged.update(elem)
        return read_shape(merged, registry)

    raise LoadError(f"cannot read shape from {node!r} (type {type(node).__name__})")


# ---------- flow `inputs:` declarations ----------


@dataclass(frozen=True)
class InputDecl:
    """One flow-input parameter: the seeding pipeline's `IOField` duck-type + a `Shape`.

    `.name`/`.type`/`.default` are what `state.seeding` (coerce_inputs / apply_defaults)
    reads — so `.type` carries the canonical Python-surface name (`str`/`int`/`float`/
    `bool`/...), which `coerce_param` matches to coerce a passed string (`"30"` -> `30`).
    `.shape` is the resolved runtime `Shape` (via `read_shape`) used for ref checks; a
    non-scalar `.type` is passed through unchanged (lists/records aren't coerced by
    `coerce_param` anyway).
    """

    name: str
    type: str
    default: Any
    required: bool
    shape: Shape


def _split_default(spec: str) -> tuple[str, Optional[str]]:
    """Split a `TYPE = default` spec on the FIRST top-level `=` (outside []/{}/quotes).

    Returns `(type_part, default_part)`; `default_part` is None when there is no `=`
    (a bare type). A `==`/`>=`/`<=`/`!=` is not a default assignment and is skipped.
    """
    depth = 0
    quote: Optional[str] = None
    for i, ch in enumerate(spec):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        elif ch == "=" and depth == 0:
            prev = spec[i - 1] if i else ""
            nxt = spec[i + 1] if i + 1 < len(spec) else ""
            if prev in "=<>!" or nxt == "=":
                continue  # part of ==/<=/>=/!=, not a default assignment
            return spec[:i].strip(), spec[i + 1 :].strip()
    return spec.strip(), None


def read_flow_inputs(mapping, registry: TypeRegistry) -> list[InputDecl]:
    """Read a Compose `inputs:` mapping into the seeding pipeline's `InputDecl`s.

    A `str` value is the `TYPE [= default]` / `Optional[X]` form; a `dict` value is a
    record type (resolved via `read_shape`, no default — decision D-DEFAULTS dropped the
    `{type:, default:}` escape-hatch map).
    """
    decls: list[InputDecl] = []
    for name, value in (mapping or {}).items():
        if isinstance(value, str):
            type_part, default_part = _split_default(value)
            shape = read_shape(type_part, registry)
            default: Any = None
            if default_part is not None and default_part != "":
                try:
                    default = yaml.safe_load(default_part)
                except yaml.YAMLError as exc:
                    raise LoadError(
                        f"input {name!r}: bad default {default_part!r}: {exc}"
                    ) from exc
            try:
                parsed = parse_type(type_part)
            except TypeCheckError as exc:
                raise LoadError(f"input {name!r}: {exc}") from exc
            engine_type = parsed.name if isinstance(parsed, ScalarType) else type_part
            required = not shape.nullable and default is None
            decls.append(InputDecl(name, engine_type, default, required, shape))
        elif isinstance(value, dict):
            shape = read_shape(value, registry)
            decls.append(InputDecl(name, value, None, True, shape))
        else:
            raise LoadError(
                f"input {name!r}: declaration must be a type string or a record map, "
                f"got {type(value).__name__}"
            )
    return decls
