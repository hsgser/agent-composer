"""P3 0c: the generic `_apply_grow` core splices a self-describing Subgraph.

Drives `FlowEngine._apply_grow` directly with a tiny one-node child Subgraph (a leaf carrying a
baked `commit_as`) and a no-op residual, asserting the generic core's contract: the node is spliced
into `flow.nodes`, registered in the state manager, and its root scheduled. `_grow_residual` is a
no-op stub this phase, so no per-kind policy runs — the core is exercised in isolation."""

from agent_composer.compile.model import END_ID, START_ID
from agent_composer.nodes.base import Grow, Subgraph
from agent_composer.runtime.engine import FlowEngine
from tests.engine._fakes import FuncNode
from tests.engine._graph_builder import _graph


def _parent_engine() -> FlowEngine:
    # A minimal parent flow with one spawner-standin body node. The engine is constructed but
    # not driven — `_apply_grow` is called directly (paused is empty, so scheduled roots land in
    # self.ready).
    flow = _graph([FuncNode("s", lambda p: {"output": "x"})],
                  [(START_ID, "s"), ("s", END_ID)])
    return FlowEngine(flow, num_workers=0)


def _one_node_grow(spawner_id: str) -> Grow:
    # A single-node child subgraph: a leaf whose Output commits under the spawner id (baked
    # commit_as), rooted at itself.
    child = FuncNode(f"{spawner_id}/leaf", lambda p: {"output": "y"})
    child.commit_as = spawner_id
    sg = Subgraph(nodes={child.id: child}, edges=[], wiring={child.id: {}}, roots=[child.id])
    return Grow(sg)


def test_apply_grow_splices_registers_and_schedules(monkeypatch):
    eng = _parent_engine()
    grow = _one_node_grow("s")
    child_id = "s/leaf"
    # Keep the residual a no-op (its per-kind body is filled by the spawner-migration phases).
    monkeypatch.setattr(eng, "_grow_residual", lambda spawner_id, g: None)

    eng._apply_grow("s", grow)

    # Spliced into the live topology.
    assert child_id in eng.flow.nodes
    # Registered in the state manager overlay.
    assert child_id in eng.sm.node_state
    # Its root scheduled (paused is empty -> lands in the serial ready deque).
    assert child_id in list(eng.ready)


def test_apply_grow_schedule_false_suppresses_scheduling(monkeypatch):
    eng = _parent_engine()
    grow = _one_node_grow("s")
    child_id = "s/leaf"
    monkeypatch.setattr(eng, "_grow_residual", lambda spawner_id, g: None)

    eng._apply_grow("s", grow, schedule=False)

    # Splice + register happen on replay, but nothing is scheduled.
    assert child_id in eng.flow.nodes
    assert child_id in eng.sm.node_state
    assert child_id not in list(eng.ready)
