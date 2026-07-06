"""P3: the Grow outcome + Flow value type (self-describing spawner expansion)."""
from agent_composer.compile.model import Flow
from agent_composer.nodes.base import Grow


def test_flow_holds_fragment_and_start_id():
    sg = Flow(nodes={}, edges=[], wiring={}, start_id="r", end_id="r")
    assert sg.start_id == "r" and sg.nodes == {} and sg.edges == [] and sg.wiring == {}


def test_grow_defaults_to_empty_prune_and_none_seed():
    sg = Flow(nodes={}, edges=[], wiring={}, start_id="r", end_id="r")
    g = Grow(sg)
    assert g.subgraph is sg
    assert g.prune == frozenset()
    assert g.seed is None


def test_grow_carries_seed():
    sg = Flow(nodes={}, edges=[], wiring={}, start_id="r", end_id="r")
    g = Grow(sg, seed={"x": 1})
    assert g.seed == {"x": 1}


def test_grow_is_a_noderesult_member():
    from agent_composer.nodes.base import NodeResult  # Union includes Grow
    import typing
    assert Grow in typing.get_args(NodeResult)
