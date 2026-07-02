"""Expansion descriptors — durability metadata for runtime-grown subgraphs.

These land the kill-recovery half of durable suspension. The WRITE half: a node returning
`Enqueue` (REF / CALL / MAP / AGENT-pause) grows the live graph at runtime; the dispatcher
captures each expansion as one of these descriptors and accumulates them in an ordered
ledger on `FlowEngine.expansions`, which `snapshot()` persists. The READ half
(`FlowEngine._replay_expansions`): on restore the engine **replays the descriptor tree
top-down** via the shared `_grow_*` clone+register helpers (effects suppressed), using the
pure id minting (`ns(callsite, child_id)`) to re-key every cloned subnode identically — so a
run paused mid-expansion resumes in a fresh process.

Closed sum (one variant per spawner kind, mirroring `_apply_enqueue`'s arms):

- `CallExpansion(spawner_id, record, children)` — REF/CALL spawned ONE child.
- `MapExpansion(spawner_id, records, children_per_element)` — MAP spawned N children.
- `AgentExpansion(spawner_id, segments)` — AGENT paused K times; one segment per pause.
- `LoopExpansion(spawner_id, records, children_per_iter)` — LOOP grew one body clone per
  iteration; `records[i]` is iteration `i`'s seed carried record. In-memory only for now —
  durable replay of a live loop is deferred (`_replay_expansions` raises for it).

Nested expansions (a REF inside a cloned child, an inner MAP, etc.) appear as
children of their enclosing descriptor — uniform recursion at any depth.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class AgentSegment(BaseModel):
    """One pause segment of an AGENT expansion.

    Carries the two dicts `agent_step` produces
    (`agent_composer/nodes/agent/modes/tool_calling.py:110-126`): `hi_desc`
    (the human_input leaf) + `resume_desc` (the resume_agent continuation with the
    full re-entry frame: memo, iterations, pending, llm_config, tools, controls, mode).

    `children` is RESERVED FOR FUTURE USE — today no node kind nested inside the
    resumed AgentNode emits a non-AGENT Enqueue
    (`agent_composer/nodes/agent/modes/tool_calling.py:127` only emits
    AGENT-target pairs), so `children` is always [] and `_replay_expansions` folds
    over an empty list for AGENT segments. Kept as a slot so the descriptor shape can
    absorb a future REF-from-tool feature without a CHECKPOINT_VERSION migration;
    `_replay_expansions` already threads `is_top_level=False` into this slot so such a
    future child would nest correctly (it would NOT be promoted to a top-level ledger entry).
    """

    hi_desc: dict[str, Any]
    resume_desc: dict[str, Any]
    children: list["Expansion"] = Field(default_factory=list)


class CallExpansion(BaseModel):
    type: Literal["call_expansion"] = "call_expansion"
    spawner_id: str
    record: dict[str, Any]
    children: list["Expansion"] = Field(default_factory=list)


class MapExpansion(BaseModel):
    type: Literal["map_expansion"] = "map_expansion"
    spawner_id: str
    records: list[dict[str, Any]]
    children_per_element: list[list["Expansion"]] = Field(default_factory=list)


class AgentExpansion(BaseModel):
    type: Literal["agent_expansion"] = "agent_expansion"
    spawner_id: str
    segments: list[AgentSegment] = Field(default_factory=list)


class LoopExpansion(BaseModel):
    """Ledger entry for a `loop` spawner: one `records[i]` per iteration seed grown so far,
    with a per-iteration nested-expansion slot (`children_per_iter[i]`). Slice 1 uses it
    in-memory only; durable replay (re-grow `#0..#i` from the recorded seeds) is deferred —
    `_replay_expansions` raises `NotImplementedError` for this variant rather than
    reconstructing iterations."""

    type: Literal["loop_expansion"] = "loop_expansion"
    spawner_id: str
    records: list[dict[str, Any]] = Field(default_factory=list)
    children_per_iter: list[list["Expansion"]] = Field(default_factory=list)


Expansion = Annotated[
    Union[CallExpansion, MapExpansion, AgentExpansion, LoopExpansion],
    Field(discriminator="type"),
]

# Rebuild forward refs (each class's `children: list["Expansion"]`).
CallExpansion.model_rebuild()
MapExpansion.model_rebuild()
AgentExpansion.model_rebuild()
LoopExpansion.model_rebuild()
AgentSegment.model_rebuild()
