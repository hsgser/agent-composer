from agent_composer.nodes.base import NodeKind
from agent_composer.runtime.eval_node import _SPAWNER_KINDS

def test_loop_is_a_spawner_kind():
    assert NodeKind.LOOP in _SPAWNER_KINDS
    assert NodeKind.LOOP.value == "loop"
