"""The pure LOOP builder `loop_iteration_subgraph` — pins its output to `_grow_loop`'s clone shape.

`loop_iteration_subgraph(child, spawner_id, record, iteration)` wraps `clone_child` at the
per-iteration callsite `f"{spawner}#{iteration}"`, bakes `commit_as=spawner_id` on the cloned body
END filler (so its Output routes to `_loop_step` via `target in self.loop_desc`, NOT the generic
commit), and returns a `Subgraph` (one loop iteration's body fragment). This test pins: the root is
the namespaced body START; the terminal is the namespaced body END carrying `commit_as ==
spawner_id`; and every node id is `ns`-prefixed under the per-iteration callsite.
"""

from agent_composer.compile.expand import loop_iteration_subgraph, map_callsite, ns
from agent_composer.compile.model import END_ID
from agent_composer.nodes.base import Subgraph

from tests.engine.test_expand import _child_flow


def test_loop_iteration_subgraph_matches_grow_loop_shape():
    child = _child_flow()
    sg = loop_iteration_subgraph(child, spawner_id="lp", record={"x": 1}, iteration=0)

    assert isinstance(sg, Subgraph)
    callsite = map_callsite("lp", 0)                      # "lp#0"
    # Root == the namespaced body START (the sole seed point).
    assert sg.roots == [ns(callsite, child.start_id)]
    # Terminal == the namespaced body END; its Output routes to _loop_step under the spawner id.
    terminal = sg.nodes[ns(callsite, END_ID)]
    assert terminal.commit_as == "lp"
    # Every cloned node id is namespaced under the per-iteration callsite.
    assert all(nid.startswith(callsite + "/") for nid in sg.nodes)
    assert set(sg.nodes) == {ns(callsite, nid) for nid in child.nodes}
