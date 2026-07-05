from agent_composer.compile.expand import map_callsite, ns
from agent_composer.nodes.base import Grow, NodeKind, Output
from agent_composer.nodes.loop import LoopNode

from tests.engine.test_expand import _child_flow


def test_loop_is_a_spawner_kind():
    assert LoopNode.is_spawner is True
    assert NodeKind.LOOP.value == "loop"


def test_loopnode_run_grows_iteration_zero_when_predicate_holds():
    # `while not ${exited}` on a seed with exited=False -> the predicate holds, so turn 0 grows
    # iteration #0 as a self-describing Grow carrying `seed=(record, 0)` for durable replay.
    child = _child_flow()
    node = LoopNode(
        "chat_loop", flow_id="chat_turn", child=child,
        predicate_kind="while", predicate="not ${exited}", max_iters=1000,
    )
    seed = {"messages": [], "exited": False}
    result = node.run(dict(seed))
    assert isinstance(result, Grow)
    assert result.seed == (seed, 0)
    # the grown subgraph is iteration #0's body, namespaced under the per-iteration callsite.
    callsite = map_callsite("chat_loop", 0)
    assert result.subgraph.roots == [ns(callsite, child.start_id)]


def test_loopnode_run_commits_seed_when_while_predicate_false():
    # `while ${exited}` on a seed with exited=False -> 0 body runs: commit the seed unchanged under
    # the spawner id via Output(commit_as). No child clone is built (returns before the grow).
    node = LoopNode(
        "chat_loop", flow_id="chat_turn", child=object(),
        predicate_kind="while", predicate="${exited}", max_iters=1000,
    )
    seed = {"messages": [], "exited": False}
    result = node.run(dict(seed))
    assert isinstance(result, Output)
    assert result.value == seed
    assert result.commit_as == "chat_loop"
