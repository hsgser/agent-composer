from types import SimpleNamespace

import pytest

from agent_composer.nodes.base import Grow, NodeKind
from agent_composer.nodes.call import CallNode
from agent_composer.nodes.map import MapNode


def _child():
    # a stub baked child: run only reads self.child for the not-baked guard + threads
    # it into the Grow subgraph; apply_defaults reads the (empty) child_inputs decls.
    return SimpleNamespace(nodes={}, edges=[], wiring={}, outputs=[])


def test_call_node_is_ref_only():
    ref = CallNode("c", flow_id="child", child=_child())
    assert ref.kind == NodeKind.CALL
    assert not hasattr(ref, "over")                   # REF carries no over/parallel
    assert not hasattr(ref, "parallel")


def test_map_node_kind_and_parallel_fields():
    mp = MapNode("m", flow_id="child", child=_child(), parallel=True)
    assert mp.kind == NodeKind.MAP
    assert mp.parallel is True
    assert not hasattr(mp, "over")                    # the SOURCE rides flow.wiring, not the node
    assert MapNode("d", flow_id="child", child=_child()).parallel is False


def test_call_ref_mode_returns_grow():
    # CALL is self-describing now: run builds the child subgraph and returns a Grow whose seed is
    # the raw call-arg record (the durable builder input); the root is the namespaced child START.
    from agent_composer.compile.expand import ns
    from agent_composer.compile.model import END_ID
    from tests.engine.test_expand import _child_flow

    node = CallNode("c", flow_id="child", child=_child_flow(), child_inputs=[])
    out = node.run({"topic": "ACME"})
    assert isinstance(out, Grow)
    assert out.seed == {"topic": "ACME"}
    assert out.subgraph.roots == [ns("c", _child_flow().start_id)]
    assert out.subgraph.nodes[ns("c", END_ID)].commit_as == "c"


def test_map_returns_grow():
    # MAP is self-describing now: run builds the whole fan-in subgraph (a synthetic map#/__start__ +
    # N child clones + a list END) and returns a Grow whose seed is the raw per-element records.
    from agent_composer.compile.expand import map_callsite, ns
    from agent_composer.compile.model import END_ID, START_ID
    from tests.engine.test_expand import _child_flow

    node = MapNode("m", flow_id="child", child=_child_flow(), child_inputs=[])
    out = node.run({"over": ["ACME", "BETA"]}, bind_item=lambda el: {"x": el})
    assert isinstance(out, Grow)
    assert out.seed == [{"x": "ACME"}, {"x": "BETA"}]
    # The single root is the synthetic map#/__start__ (it fans out to the element starts on run);
    # the list END commits under the spawner.
    assert out.subgraph.roots == [ns("m", START_ID)]
    for i in range(2):
        assert ns(map_callsite("m", i), _child_flow().start_id) in out.subgraph.nodes
    assert out.subgraph.nodes[ns("m", END_ID)].commit_as == "m"


def test_map_empty_returns_grow_with_lone_list_end():
    from agent_composer.compile.expand import ns
    from agent_composer.compile.model import END_ID, START_ID
    from tests.engine.test_expand import _child_flow

    node = MapNode("m", flow_id="child", child=_child_flow(), child_inputs=[])
    out = node.run({"over": []}, bind_item=lambda el: {"x": el})
    assert isinstance(out, Grow)
    assert out.seed == []
    # N=0: the body is the synthetic start + the list END; the root is the synthetic start, which
    # wires straight to the list END (emits []).
    map_start_id = ns("m", START_ID)
    map_end_id = ns("m", END_ID)
    assert set(out.subgraph.nodes) == {map_start_id, map_end_id}
    assert out.subgraph.roots == [map_start_id]


def test_call_unbaked_child_raises():
    node = CallNode("c", flow_id="child", child=None)
    with pytest.raises(RuntimeError, match="not baked"):
        node.run({"topic": "ACME"})


def test_map_unbaked_child_raises():
    node = MapNode("m", flow_id="child", child=None)
    with pytest.raises(RuntimeError, match="not baked"):
        node.run({"over": ["ACME"]}, bind_item=lambda el: {"topic": el})
