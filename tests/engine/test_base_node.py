"""Unit tests for the Node.run() contract pipeline."""

from agent_composer.events import (
    NodeFailed,
    NodeRouted,
    NodeStarted,
    NodeSucceeded,
    PauseRequested,
    StreamChunk,
)
from agent_composer.typesys.pool import VariablePool
from agent_composer.typesys.values import ValueKind, Type
from tests.engine._fakes import (
    BranchNode,
    FailNode,
    FuncNode,
    PauseOnceNode,
    ReturnsFailedNode,
    StreamNode,
    drive,
)


def _events(node):
    return list(drive(node))


def test_result_node_emits_started_then_succeeded():
    evs = _events(FuncNode("n", lambda p: {"output": "ok"}))
    assert isinstance(evs[0], NodeStarted)
    assert isinstance(evs[-1], NodeSucceeded)
    assert evs[-1].output == {"output": "ok"}


def test_streaming_node_emits_chunks_then_succeeded():
    evs = _events(StreamNode("n", ["a", "b", "c"]))
    kinds = [type(e).__name__ for e in evs]
    assert kinds == ["NodeStarted", "StreamChunk", "StreamChunk", "StreamChunk", "NodeSucceeded"]
    assert [e.chunk for e in evs if isinstance(e, StreamChunk)] == ["a", "b", "c"]
    assert evs[-1].output == {"output": "abc"}


def test_raising_node_emits_failed_not_succeeded():
    evs = _events(FailNode("n", "kaboom"))
    assert isinstance(evs[-1], NodeFailed)
    assert evs[-1].error == "kaboom"
    assert evs[-1].error_type == "RuntimeError"
    assert not any(isinstance(e, NodeSucceeded) for e in evs)


def test_node_returning_failed_status():
    evs = _events(ReturnsFailedNode("n"))
    assert isinstance(evs[-1], NodeFailed)
    assert evs[-1].error == "declined"
    assert evs[-1].error_type == "Policy"


def test_branch_node_carries_handle():
    evs = _events(BranchNode("n", "case_a"))
    assert isinstance(evs[-1], NodeRouted)
    assert evs[-1].handle == "case_a"


def test_pause_stops_without_terminal_event():
    node = PauseOnceNode("n")
    evs = _events(node)  # empty pool -> suspends
    assert isinstance(evs[0], NodeStarted)
    assert isinstance(evs[-1], PauseRequested)
    assert evs[-1].reason == "needs-input"
    assert not any(isinstance(e, (NodeSucceeded, NodeFailed)) for e in evs)


def test_node_does_not_write_pool():
    pool = VariablePool()
    list(drive(FuncNode("n", lambda p: {"output": "x"}), pool))
    # the node described an output but must NOT have written it; that's the engine's job
    assert pool.get("n") is None


def test_node_carries_declared_output_type():
    assert FuncNode("n", lambda p: {}).output_type is None
    typ = Type.scalar(ValueKind.NUMBER)
    n = FuncNode("n", lambda p: {}, output_type=typ)
    assert n.output_type == typ
