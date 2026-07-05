"""P3: the Grow outcome + Subgraph value type (self-describing spawner expansion)."""
from agent_composer.nodes.base import Grow, Subgraph


def test_subgraph_holds_flow_fragment_and_roots():
    sg = Subgraph(nodes={}, edges=[], wiring={}, roots=["r"])
    assert sg.roots == ["r"] and sg.nodes == {} and sg.edges == [] and sg.wiring == {}


def test_grow_defaults_to_empty_prune_and_none_seed():
    sg = Subgraph(nodes={}, edges=[], wiring={}, roots=[])
    g = Grow(sg)
    assert g.subgraph is sg
    assert g.prune == frozenset()
    assert g.seed is None


def test_grow_carries_seed():
    sg = Subgraph(nodes={}, edges=[], wiring={}, roots=[])
    g = Grow(sg, seed={"x": 1})
    assert g.seed == {"x": 1}


def test_grow_is_a_noderesult_member():
    from agent_composer.nodes.base import NodeResult  # Union includes Grow
    import typing
    assert Grow in typing.get_args(NodeResult)
