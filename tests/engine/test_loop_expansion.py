"""LoopExpansion — the closed-union ledger member for a `loop` spawner.

A loop is a driver whose live growth (one cloned body per iteration) is recorded as a
`LoopExpansion` in the `FlowEngine.expansions` ledger, exactly as CALL/MAP/AGENT record
their own. It must be a proper member of the closed discriminated `Expansion` union (so the
`_replay_expansions` match stays exhaustive) — durable REPLAY of a live loop is deferred, so
that arm raises `NotImplementedError` rather than reconstructing iterations.
"""

from pydantic import TypeAdapter

from agent_composer.suspension.expansions import Expansion, LoopExpansion


def test_loop_expansion_shape_and_discriminator():
    d = LoopExpansion(spawner_id="chat_loop", records=[{"exited": False}], children_per_iter=[[]])
    assert d.type == "loop_expansion"
    assert d.spawner_id == "chat_loop"
    assert d.records[0] == {"exited": False}
    assert d.model_copy(deep=True).records == d.records


def test_loop_expansion_is_a_union_member():
    parsed = TypeAdapter(Expansion).validate_python(
        {"type": "loop_expansion", "spawner_id": "x", "records": [], "children_per_iter": []}
    )
    assert isinstance(parsed, LoopExpansion)
