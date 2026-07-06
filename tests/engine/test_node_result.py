"""The `NodeResult` closed sum: Output | Route | Pause | Grow.

The FAILED path is `raise` -> engine boundary `NodeFailed` (no `Failure` variant). `Grow` is
defined in `nodes.base` but produced by spawner nodes and interpreted by the dispatcher's
`_apply_grow`. Routing rides `Route` (tested in `test_route.py`).
"""

from agent_composer.compile.model import Flow
from agent_composer.nodes.base import Grow, Output, Pause


def test_output_carries_value():
    assert Output(value=3).value == 3


def test_pause_carries_reason():
    assert Pause(reason="needs-input").reason == "needs-input"


def test_grow_is_defined():
    sg = Flow(nodes={}, edges=[], wiring={}, start_id="", end_id="")
    g = Grow(subgraph=sg, seed={"x": 1})
    assert g.subgraph is sg
    assert g.seed == {"x": 1}
    assert g.prune == frozenset()
