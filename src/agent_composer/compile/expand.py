"""Runtime graph-expansion machinery — the pure half.

When a spawner (REF / MAP / agent-pause) runs, it does not run a child engine; it
returns a *description* (`Enqueue`) and the engine GROWS the live graph by cloning the
target child(ren) deep-namespaced into the running `CompiledFlow`. This module holds the
**pure** machinery that growth keys off:

- `ns` / `map_callsite` / `ask_resume_edge_id`: deterministic id minting. Every
  cloned node/edge id is a pure function of `(callsite, child static id, element index)` —
  NO emission counter — so a re-clone on kill-recovery re-keys identically.

The pure cloner (`clone_child` / `ClonedSubgraph`) splices the child's own
`START_ID..END_ID` (every flow is `START_ID -> body -> END_ID`): the child `START_ID` is the alias-
seed point — SEEDED WITH THE CALL-ARGS AS EDGES (no `_rens` literal-baking) — and the child
`END_ID` is the alias filler. A child node reading `${input.X}` is re-pointed to the namespaced
child START_ID's output object (`${<callsite>/<start>.output.X}`); the dispatcher
consumes the descriptions and performs the (impure) `add_subgraph` + `register` + seed.

Layer: compile — imports `nodes`/`model`/`expr` (ladder-legal); never `runtime`.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional

from agent_composer.compile.model import Edge, START_ID
from agent_composer.expr import rewrite_template_refs
from agent_composer.nodes.base import Node


def ns(callsite: str, child_id: str) -> str:
    """Namespace a child node/edge id under its callsite: `<callsite>/<child_id>`.
    `callsite` = spawner id (REF/agent) or `f"{spawner}#{i}"` (MAP element i); nests."""
    return f"{callsite}/{child_id}"


def map_callsite(spawner_id: str, i: int) -> str:
    """The per-element callsite for MAP element `i`: `f"{spawner}#{i}"`."""
    return f"{spawner_id}#{i}"


def ask_resume_edge_id(callsite: str) -> str:
    """The agent continuation edge id: `f"{callsite}/__ask_resume#0"`."""
    return f"{callsite}/__ask_resume#0"


# --------------------------------------------------------------------------- #
# clone_child — the pure deep-flatten + partial-eval + arity cloner
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClonedSubgraph:
    """The pure result of cloning a child flow at one callsite.

    `nodes`/`edges`/`wiring` are deep-namespaced under the callsite (the dispatcher appends
    them to the live `CompiledFlow` via `add_subgraph`); `roots` is the namespaced child `START_ID`
    (the sole seed point — `[ns(callsite, child.start_id)]`); `out_node_id` is the namespaced
    child `END_ID` (the alias filler for REF / one element input for MAP). `boundary_asserts` are
    the child's BOUNDARY asserts exposed RAW (un-namespaced — they read `${inputs}/${system}`)
    for the dispatcher to evaluate eagerly against the baked record in `_apply_enqueue` (fired only
    there, NOT off the spliced child START_ID)."""

    nodes: dict[str, Node]
    edges: list[Edge]
    wiring: dict[str, dict[str, Any]]
    roots: list[str]
    out_node_id: str
    boundary_asserts: list[str] = field(default_factory=list)


def _whole_span(src: str) -> Optional[str]:
    """If `src` is EXACTLY one `${...}` span, return its interior; else None."""
    if not isinstance(src, str) or not (src.startswith("${") and src.endswith("}")):
        return None
    interior = src[2:-1]
    if "${" in interior or "}" in interior:  # embedded / nested — not a single bare span
        return None
    return interior


def _rens_internal(src: Any, callsite: str) -> Any:
    """Re-namespace one binding source under `callsite` — NO baking.

    Singular only:
    - `${input.<k>...}`   -> `${<callsite>/<start>.output.<k>...}` (namespaced child
      START_ID's output object read via the node-first head)
    - `${<X>.output...}`  -> `${<callsite>/<X>.output...}` (node-first re-namespaced in place)
    - `${system.X}` (run-global) and other heads untouched.

    Legacy plural heads (`outputs.X` / `inputs.X`) are rejected at parse time.

    Routes every `${...}` span through `rewrite_template_refs` + the ONE parse tree, so an
    embedded / coalesce / whole-expression span is re-namespaced leaf-by-leaf (the old flat
    `${...}` regex only handled a simple single-path span)."""
    if not isinstance(src, str):
        return src

    def _rename(path: str) -> "str | None":
        parts = path.split(".")
        if len(parts) >= 2 and parts[0] == "input":
            # input.k[.rest] -> <callsite>/<start>.output.k[.rest]
            key = parts[1]
            rest = ".".join(parts[2:])
            new = f"{ns(callsite, START_ID)}.output.{key}"
            return new + (f".{rest}" if rest else "")
        # Node-first: <X>.output[.rest] -> <callsite>/<X>.output[.rest]
        if len(parts) >= 2 and parts[1] == "output":
            new = f"{ns(callsite, parts[0])}.output"
            return new + ("." + ".".join(parts[2:]) if len(parts) > 2 else "")
        return None  # system.X / any other head untouched

    return rewrite_template_refs(src, _rename)


def clone_child(child, callsite: str, record: dict) -> ClonedSubgraph:
    """Splice a child `CompiledFlow`'s `START_ID..END_ID` at `callsite`. Every child node
    (incl. its `START_ID`/`END_ID`) is cloned deep-namespaced; the child `START_ID` is SEEDED with the
    call-args as edges (no baking); the child `END_ID` is the alias filler. Pure — the dispatcher
    performs the impure `add_subgraph`/`register`/seed."""
    nodes: dict[str, Node] = {}
    for nid, node in child.nodes.items():
        clone = copy.deepcopy(node)
        clone.id = ns(callsite, nid)
        nodes[clone.id] = clone

    # Re-namespace EVERY node's wiring (internal ${X.output}/${input.X} re-pointed; no baking).
    wiring: dict[str, dict[str, Any]] = {}
    for nid, w in child.wiring.items():
        wiring[ns(callsite, nid)] = {p: _rens_internal(src, callsite) for p, src in w.items()}

    # Re-key ALL internal edges (incl. START_ID->body and body->END_ID) identically — START_ID/END_ID are
    # ordinary nodes with reserved ids now (no __start__/__end__ sentinel special-cases).
    edges: list[Edge] = []
    for e in child.edges:
        edges.append(Edge(
            id=ns(callsite, e.id),
            from_=ns(callsite, e.from_),
            to=ns(callsite, e.to),
            source_handle=e.source_handle,
            input_group=e.input_group,
            optional=e.optional,
            ordering=e.ordering,
        ))

    # Seed the child START_ID with the call-args AS EDGES: a ${...} forward-ref value mints
    # a producer->START_ID edge; a literal is a constant seed with no edge. The child START_ID is the
    # sole seed point (it OVERRIDES the provisional ${input.X} wiring the loader left on it).
    start_ns = ns(callsite, child.start_id)
    start_wiring = wiring.setdefault(start_ns, {})
    start_wiring.clear()                       # drop the provisional `{name: ${input.name}}`
    for param, value in record.items():
        start_wiring[param] = value
        if isinstance(value, str) and "${" in value:
            producer = _producer_of(value)
            if producer is not None:
                edges.append(Edge(
                    id=f"{producer}->{start_ns}#0",
                    from_=producer,
                    to=start_ns,
                    input_group=param,
                ))
    roots = [start_ns]

    # The child END_ID is the alias filler (its producer->END_ID edges are already re-keyed above).
    out_id = ns(callsite, child.end_id)

    # Carry the child AssertSet: boundary RAW (un-namespaced) for the dispatcher's eager eval
    # ONLY (never fired off the spliced START_ID); post re-homed onto the cloned child END_ID.
    asserts = getattr(child, "child_asserts", None)
    boundary_asserts = list(asserts.boundary) if asserts is not None else []
    nodes[out_id].post_asserts = [
        _rens_internal(a, callsite) for a in (asserts.post if asserts is not None else [])
    ]

    return ClonedSubgraph(
        nodes=nodes,
        edges=edges,
        wiring=wiring,
        roots=roots,
        out_node_id=out_id,
        boundary_asserts=boundary_asserts,
    )


def _producer_of(src: str) -> Optional[str]:
    """The producer node id of a forward-ref record value, else None.
    Singular only: `${<producer>.output[.…]}`."""
    whole = _whole_span(src)
    if whole is None:
        return None
    parts = whole.split(".")
    if len(parts) >= 2 and parts[1] == "output":
        return parts[0]
    return None


# --------------------------------------------------------------------------- #
# clone_continuation_pair — the agent-pause continuation cloner
# --------------------------------------------------------------------------- #


def clone_continuation_pair(pair, callsite: str, *, output_shape=None, retries: int = 2) -> ClonedSubgraph:
    """Materialize the agent-pause continuation PAIR namespaced at `callsite`.

    `pair` is `[human_input_desc, resume_desc]` from `agent_step`'s `Enqueue`. The
    `human_input` leaf is a ROOT (no incoming edge), so the engine's leaf-pause path applies and its
    `HumanInputRequired.node_id` is the namespaced `hi_id`. The resume node is an `AgentNode`
    with a `Resume` entry (the continuation arm — same `kind = AGENT`, no separate kind); it
    reads the human's `answer` via the BARE forward-ref `${<hi_id>.output}` bound to
    its single `answer` param; the data edge for that ref is synthesized via the SAME producer
    derivation `clone_child`/`build.py` use (`f"{producer}->{consumer}#{i}"`), so the pool ref
    and the edge agree on `hi_id`. Pure — the dispatcher performs the impure
    append/register/seed.

    `output_shape`/`retries` carry the SPAWNER's declared output Shape and self-correction cap
    onto the resume node so a resumed agent with a non-text `output:` still emits the declared
    shape on its final turn (the dispatcher reads them off `flow.nodes[spawner_id]`; they are
    not serialized — restore re-grows from the compiled spawner). For a multi-pause chain each
    resume node becomes the next segment's spawner, so the shape propagates segment to segment."""
    from agent_composer.nodes.agent.node import AgentNode, Resume
    from agent_composer.nodes.human_input import HumanInputNode

    hi_desc, resume_desc = pair
    hi_id = ns(callsite, hi_desc["node_id"])              # e.g. agent/__ask#q1
    resume_id = ns(callsite, "__resume#" + hi_desc["slot"])

    hi_node = HumanInputNode(hi_id, prompt=hi_desc["prompt"])
    resume_node = AgentNode(
        resume_id,
        entry=Resume(
            memo=resume_desc["memo"],
            iterations=resume_desc["iterations"],
            pending=resume_desc["pending"],
        ),
        llm_config=resume_desc.get("llm_config"),
        tools=resume_desc.get("tools"),
        controls=resume_desc.get("controls"),
        mode=resume_desc.get("mode", "tool_calling"),
        retries=retries,
    )
    # The Resume entry's declared output Shape is set as node DATA (AgentNode.__init__ takes no
    # output_shape param — the compiler stamps it; here the dispatcher supplies the spawner's).
    resume_node.output_shape = output_shape

    # Rewrite the answer forward-ref to the NAMESPACED node-first ref.
    answer_ref = f"${{{hi_id}.output}}"
    producer = _producer_of(answer_ref)                  # == hi_id
    assert producer is not None                          # a well-formed `${id.output}` always resolves
    edge = Edge(
        id=f"{producer}->{resume_id}#0",                 # same shape as build.py's producer edges
        from_=producer,
        to=resume_id,
        input_group="answer",
    )

    return ClonedSubgraph(
        nodes={hi_id: hi_node, resume_id: resume_node},
        edges=[edge],
        wiring={hi_id: {}, resume_id: {"answer": answer_ref}},
        roots=[hi_id],
        out_node_id=resume_id,
    )
