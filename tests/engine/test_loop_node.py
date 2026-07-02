from agent_composer.nodes.base import Enqueue, NodeKind
from agent_composer.nodes.loop import LoopNode
from agent_composer.runtime.eval_node import _SPAWNER_KINDS

def test_loop_is_a_spawner_kind():
    assert NodeKind.LOOP in _SPAWNER_KINDS
    assert NodeKind.LOOP.value == "loop"


def test_loopnode_run_enqueues_child_with_seed_record():
    node = LoopNode(
        "chat_loop", flow_id="chat_turn", child=object(),
        predicate_kind="while", predicate="not ${exited}", max_iters=1000,
    )
    seed = {"messages": [], "exited": False}
    result = node.run(dict(seed))
    assert isinstance(result, Enqueue)
    assert result.target is node.child
    assert result.inputs == seed
