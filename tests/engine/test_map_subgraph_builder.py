"""The pure MAP builder `map_subgraph` — pins its output to `_grow_map`'s clone+fan-in shape.

`map_subgraph(child, spawner_id, records)` clones the child once per element (deep-namespaced under
`map_callsite(spawner_id, i)`), synthesizes the list-`END` fan-in (an `EndNode.list_` carrying
`commit_as=spawner_id`, fed one `e{i}` group per element), and returns a `Subgraph`. This test pins:
over 2 records there are 2 child clones + 1 list-END; the list-END commits under the spawner; the
end-wiring has `e0`/`e1`. The N=0 case pins that the sole node is the list-END and that it is a root
(0-incoming, so it schedules and emits `[]`).
"""

from agent_composer.compile.expand import map_callsite, map_subgraph, ns
from agent_composer.compile.model import END_ID
from agent_composer.nodes.base import Subgraph

from tests.engine.test_expand import _child_flow


def test_map_subgraph_matches_grow_map_shape():
    child = _child_flow()
    records = [{"x": 1}, {"x": 2}]
    sg = map_subgraph(child, spawner_id="m0", records=records)

    assert isinstance(sg, Subgraph)
    map_end_id = ns("m0", END_ID)

    # 2 child clones (one per element callsite) + 1 synthesized list-END.
    for i in range(2):
        callsite = map_callsite("m0", i)
        assert set(ns(callsite, nid) for nid in child.nodes) <= set(sg.nodes)
    assert map_end_id in sg.nodes

    # The list-END commits its list Output under the spawner id.
    assert sg.nodes[map_end_id].commit_as == "m0"

    # The fan-in wiring has one e{i} group per element.
    end_wiring = sg.wiring[map_end_id]
    assert set(end_wiring) == {"e0", "e1"}
    for i in range(2):
        out_id = ns(map_callsite("m0", i), child.end_id)
        assert end_wiring[f"e{i}"] == f"${{{out_id}.output}}"

    # Element roots (the namespaced child STARTs) are present in roots; N>0 so the
    # list-END is NOT a root (it has incoming fan-in edges).
    for i in range(2):
        assert ns(map_callsite("m0", i), child.start_id) in sg.roots
    assert map_end_id not in sg.roots


def test_map_subgraph_n0_lists_end_is_the_sole_root():
    child = _child_flow()
    sg = map_subgraph(child, spawner_id="m0", records=[])

    map_end_id = ns("m0", END_ID)
    # The only node is the list-END; it has 0 incoming edges so it MUST be a root
    # (it schedules and emits []).
    assert set(sg.nodes) == {map_end_id}
    assert sg.edges == []
    assert map_end_id in sg.roots
    assert sg.nodes[map_end_id].commit_as == "m0"
