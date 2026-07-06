"""Flow-input default seeding — shared by the run boundary and REF/MAP children.

The Compose run boundary coerces + defaults the top-level run args; a REF/MAP child
runs via the engine (not the boundary), so its `_run` reuses `apply_defaults` to fill
the child's own declared defaults for omitted args. The clock reaches children through
the inherited `pool.system` (`${system.today}`/`${system.now}`) — there is no separate
`as_of` clock special-case.

Lives in the engine layer so the nodes can import it without an import-direction
inversion. It is spec-free at runtime — it duck-types the declared-input decls
(`.name`/`.type`/`.default`); the `InputDecl` it operates on is the Compose loader's.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:  # pragma: no cover - typing only (keeps state import-clean)
    from agent_composer.state.pool import TypedVariablePool


def coerce_param(field: Any, value: Any) -> Any:
    """Coerce a raw (usually string) input to the flow input's declared `type`.

    Only the unambiguous scalar types are coerced; string/topics/object pass
    through as-entered. Invalid numbers/bools fall back to the raw value rather
    than raising — the engine/node decides what to do with a bad input.
    """
    if value is None or not isinstance(value, str):
        return value
    raw = value.strip()
    try:
        if field.type == "int":
            return int(raw)
        if field.type == "float":
            return float(raw)
        if field.type == "bool":
            return raw.lower() in ("1", "true", "yes", "y", "on")
    except ValueError:
        return value
    return value


def coerce_inputs(inputs: List[Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    by_name = {io.name: io for io in inputs}
    return {k: coerce_param(by_name[k], v) if k in by_name else v for k, v in raw.items()}


def apply_defaults(inputs: List[Any], coerced: Dict[str, Any]) -> Dict[str, Any]:
    """Fill each declared flow input's default for any name the caller OMITTED.

    ``coerce_inputs`` only carries caller-passed keys, so an omitted optional input
    is absent here; without this it resolves to an unbound ``None`` at run time
    (and, since the strict-renderer change, RAISES at render). The default is
    coerced to the input's `type` so a YAML ``default: "30"`` on an integer input
    seeds ``30``.

    A caller-passed value is never overridden — even a falsy one, and even ``None``.
    So a parent that BINDS ``null`` to a child input SHADOWS that child's default (the
    name is present in the record): null is a value, not absence (like Python
    ``f(x=None)``). The child default fills only when the parent omits the input. The
    cross-flow type check (e06) relies on this — a nullable source needs a binding-level
    non-null guarantee (`:-literal` / `:?`), not the child's default, to fill a
    non-nullable input.
    """
    out = dict(coerced)
    for io in inputs:
        if io.default is not None and io.name not in out:
            out[io.name] = coerce_param(io, io.default)
    return out


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_utc() -> str:
    """The run's wall-clock 'now' as an ISO-8601 datetime (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def default_run_id() -> str:
    """A fresh run identifier for when the host doesn't supply one.

    A run id is inherently unique per run, so the boundary mints one (uuid4 hex)
    unless the host injects a meaningful value — mirroring how the clock ambients
    are auto-seeded, so `${system.run_id}` always resolves.
    """
    return uuid.uuid4().hex


def seed_system_clock(pool: "TypedVariablePool") -> None:
    """Seed the host-ambient clock into the pool's `system` namespace, once per run.

    `${system.today}` is a `date`; `${system.now}` a `datetime`. Computed once here and
    inherited by REF/MAP children (they copy `pool.system`), so every node, sub-flow,
    and MAP element observes one consistent clock. The clock is the only seam through
    which a flow may read "now" (the engine never resolves date words).
    """
    from agent_composer.state.segments import DateValue, DateTimeValue

    pool.system["today"] = DateValue(value=today_utc())
    pool.system["now"] = DateTimeValue(value=now_utc())
