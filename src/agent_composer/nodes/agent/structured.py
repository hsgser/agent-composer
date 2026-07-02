"""Structured AGENT output — derive a pydantic schema from a declared `output:` Shape and
generate a value that conforms to it.

An AGENT declares `output:` as a typed `Shape`. For a bare `str` (or a `Literal[...]`
variant) the agent stays a text producer — `shape_to_schema` returns `None` and the mode
takes the plain-text path. For any richer shape (a record, a scalar `int`/`float`/`bool`, a
list) `shape_to_schema` builds a `pydantic.BaseModel` the mode hands to
`with_structured_output`, so the model emits a value the engine's write boundary
(`pool.set(..., declared=output_shape)`) accepts.

Scalars and lists can't be a top-level pydantic model field on their own, so they are wrapped
in a single-field model (`{"value": <scalar>}` / `{"items": [<element>]}`); `_unwrap` strips
the wrapper back to the bare value after generation. A record maps one model field per
declared field and passes through as the dumped dict.

Layer: nodes — imports `state` (Shape/SegmentType) + `pydantic`; no engine/runtime imports.
"""

from __future__ import annotations

import json
from typing import Any, List, Literal, Optional

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, create_model

from agent_composer.state.segments import Shape, SegmentType

# SegmentType -> python type for a scalar slot. DATE/DATETIME persist as ISO strings.
_SCALAR_PY: dict[SegmentType, type] = {
    SegmentType.STRING: str,
    SegmentType.INTEGER: int,
    SegmentType.NUMBER: float,
    SegmentType.BOOLEAN: bool,
    SegmentType.DATE: str,
    SegmentType.DATETIME: str,
}


def _py_type(shape: Shape) -> Any:
    """The python annotation for a value of `shape` as a pydantic field type.

    A `STRING` with `tags` is a `Literal[...]` variant; a nested `OBJECT` with `fields`
    becomes its own submodel; a `LIST_*` becomes `list[<element type>]`. Raises `ValueError`
    for a type with no structured mapping (`NONE`, `FILE`) so a new SegmentType is loud.
    """
    seg = shape.seg_type
    if seg == SegmentType.STRING and shape.tags:
        return Literal[tuple(sorted(shape.tags))]  # type: ignore[valid-type]
    if seg in _SCALAR_PY:
        return _SCALAR_PY[seg]
    if seg == SegmentType.OBJECT:
        return _record_model(shape) if shape.fields else dict
    if seg == SegmentType.LIST_ANY:
        return list
    if seg.is_list():
        elem = shape.element
        if elem is not None:
            return List[_py_type(elem)]  # type: ignore[misc]
        scalar = seg.element_type
        return List[_SCALAR_PY[scalar]] if scalar in _SCALAR_PY else list  # type: ignore[misc]
    raise ValueError(f"shape_to_schema: no structured mapping for segment type {seg!r}")


def _record_model(shape: Shape) -> type[BaseModel]:
    """Build a pydantic model from an `OBJECT` Shape: one field per `fields` entry, required
    iff named in `required` (others default to `None`); `nullable` widens a field to Optional."""
    required = shape.required or frozenset()
    spec: dict[str, Any] = {}
    for name, sub in (shape.fields or {}).items():
        ann = _py_type(sub)
        if name in required and not sub.nullable:
            spec[name] = (ann, ...)
        else:
            spec[name] = (Optional[ann], None)
    return create_model("Record", **spec)


def shape_to_schema(shape: Shape) -> Optional[type[BaseModel]]:
    """Derive a pydantic model for `shape`, or `None` when the agent should stay a text
    producer (a bare `str` or a `Literal[...]` variant — today's passthrough).

    A record (`OBJECT` with `fields`) maps directly to a model. A scalar or a list can't be a
    standalone model, so it is wrapped in a single-field model (`value` / `items`) that
    `_unwrap` later strips.
    """
    seg = shape.seg_type
    if seg == SegmentType.STRING:
        return None  # bare str OR a Literal variant — keep the text path
    if seg == SegmentType.OBJECT and shape.fields:
        return _record_model(shape)
    if seg.is_list() or seg == SegmentType.LIST_ANY:
        return create_model("ListWrapper", items=(_py_type(shape), ...))
    # any other scalar (int/float/bool/date/datetime) or freeform object -> value wrapper
    return create_model("ScalarWrapper", value=(_py_type(shape), ...))


def _unwrap(obj: BaseModel, shape: Shape) -> Any:
    """Strip the single-field wrapper `shape_to_schema` adds for a scalar/list, returning the
    bare value the write boundary expects. A record is returned as its dumped dict."""
    data = obj.model_dump()
    seg = shape.seg_type
    if seg == SegmentType.OBJECT and shape.fields:
        return data
    if seg.is_list() or seg == SegmentType.LIST_ANY:
        return data["items"]
    return data["value"]


def _supports_native_structured(llm_config: Any) -> bool:
    """Whether the node's effective `(provider, model)` can use native structured output.

    Reads `provider`/`model` off the effective config (a plain dict on the run path, or an
    `LLMConfig` in some unit harnesses); an unset field falls back to the package env defaults
    (the same source `model_from_config` uses), so the gate matches the model the node actually
    built. Delegates the decision to the capability catalog.
    """
    from agent_composer._settings import default_llm_model, default_llm_provider
    from agent_composer.llm_clients.capabilities import supports_native_structured

    def _get(key: str) -> Any:
        if isinstance(llm_config, dict):
            return llm_config.get(key)
        return getattr(llm_config, key, None)

    provider = _get("provider") or default_llm_provider()
    model = _get("model") or default_llm_model()
    return supports_native_structured(provider, model)


def _text_of(reply: Any) -> str:
    """The text content of a model reply (a langchain message or a bare string)."""
    content = getattr(reply, "content", reply)
    return content if isinstance(content, str) else str(content)


def _strip_code_fence(text: str) -> str:
    """Strip a single wrapping Markdown code fence from `text`, returning the inner text.

    Models often wrap JSON in a ```json … ``` (or bare ``` … ```) fence despite the
    "no code fences" instruction; a bare `json.loads` then fails and burns a retry. If
    `text` opens with a ``` fence line, drop that line and a matching trailing ```;
    text without a fence is returned unchanged (surrounding whitespace trimmed).
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    newline = s.find("\n")
    if newline == -1:
        return s  # a lone ``` with no body — leave it for json.loads to reject
    inner = s[newline + 1 :]
    if inner.rstrip().endswith("```"):
        inner = inner.rstrip()[:-3]
    return inner.strip()


def generate_structured(
    model: Any,
    messages: list,
    shape: Shape,
    *,
    max_retries: int = 2,
    llm_config: dict | None = None,
) -> Any:
    """Generate a value conforming to `shape`, retrying on provider deviation up to a cap.

    Derives the pydantic schema from `shape` and picks a generation path by capability gate
    (`_supports_native_structured` on the node's effective `llm_config`):

    - **native** — bind the schema via `with_structured_output` and invoke.
    - **fallback** — the provider has no native structured output: render the JSON schema into
      the prompt, invoke for free text, then `json.loads` + `schema.model_validate` it.

    Either way a deviation (schema rejection, unparseable JSON) appends a corrective
    `HumanMessage` naming the error and retries, up to `max_retries` extra attempts
    (`max_retries + 1` total invocations). The last error is re-raised once the cap is spent.
    """
    schema = shape_to_schema(shape)
    if _supports_native_structured(llm_config or {}):
        return _generate_native(model, messages, shape, schema, max_retries)
    return _generate_fallback(model, messages, shape, schema, max_retries)


def _generate_native(
    model: Any, messages: list, shape: Shape, schema: type[BaseModel], max_retries: int
) -> Any:
    """Native path: `with_structured_output(schema)` + capped self-correction retry."""
    msgs = list(messages)
    last_err: Optional[Exception] = None
    for _ in range(max_retries + 1):
        try:
            obj = model.with_structured_output(schema).invoke(msgs)
            return _unwrap(obj, shape)
        except Exception as err:  # provider deviated from the schema; correct and retry
            last_err = err
            msgs = msgs + [
                HumanMessage(
                    content=(
                        f"Your previous output was invalid: {err}. "
                        "Respond with valid data matching the schema."
                    )
                )
            ]
    raise last_err  # type: ignore[misc]


def _generate_fallback(
    model: Any, messages: list, shape: Shape, schema: type[BaseModel], max_retries: int
) -> Any:
    """Prompt-injection path for a provider with no native structured output: ask for JSON
    matching the schema, parse + validate the free-text reply, capped self-correction retry.
    A wrapping ```json … ``` code fence is stripped before parsing (`_strip_code_fence`) so a
    fenced-but-valid reply doesn't burn a retry."""
    instruction = HumanMessage(
        content=(
            "Respond with ONLY a JSON object matching this JSON schema (no prose, no code "
            f"fences):\n{json.dumps(schema.model_json_schema())}"
        )
    )
    msgs = list(messages) + [instruction]
    last_err: Optional[Exception] = None
    for _ in range(max_retries + 1):
        try:
            text = _text_of(model.invoke(msgs))
            obj = schema.model_validate(json.loads(_strip_code_fence(text)))
            return _unwrap(obj, shape)
        except Exception as err:  # unparseable / non-conforming; correct and retry
            last_err = err
            msgs = msgs + [
                HumanMessage(
                    content=(
                        f"Your previous output was invalid: {err}. Respond with ONLY a JSON "
                        "object matching the schema."
                    )
                )
            ]
    raise last_err  # type: ignore[misc]
