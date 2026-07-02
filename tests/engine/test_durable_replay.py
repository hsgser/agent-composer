"""Restore-side replay: a deterministic fold over the persisted `expansions` descriptor
tree that re-grows a paused, runtime-GROWN run on a FRESH process.

`snapshot()` is the write half; `restore()` + `_replay_expansions` are the read half. These
tests pin: the clone+register helpers are deterministic (re-key identically),
`_replay_expansions` reproduces the live overlay byte-for-byte on a freshly recompiled flow
(a NESTED oracle), and a true cross-process `dumps->loads->restore(fresh)->resume` of a CALL
/ MAP / AGENT / nested grown run reaches the SAME terminal as the live engine.
"""

from agent_composer.compile.model import END_ID, START_ID, FlowOutput, NodeState
from agent_composer.events import RunPaused, RunSucceeded
from agent_composer.nodes.call.node import CallNode
from agent_composer.nodes.human_input import HumanInputNode
from agent_composer.nodes.map.node import MapNode
from agent_composer.runtime.engine import FlowEngine
from agent_composer.suspension.checkpoint import RunCheckpoint
from agent_composer.suspension.commands import DeliverAnswerCommand
from tests.engine._fakes import FuncNode, stamp_reads
from tests.engine._graph_builder import _graph
from tests.engine.test_engine_expansions_ledger import (
    _inner_pause_child,
    call_with_inner_pause,
    map_with_inner_pause,
)


# --- the whole-arm clone+register helpers are deterministic ------------------ #


def test_grow_call_re_registers_identical_node_ids():
    """`_grow_call` re-keys the SAME cloned node ids for the same `(spawner, child, record)`
    across two fresh engines (the clone is pure: `ns(callsite, child_id)` has no counter)."""
    from agent_composer.suspension.expansions import CallExpansion

    g1 = call_with_inner_pause()
    e1 = FlowEngine(g1)
    bridge1 = e1.flow.nodes["bridge"]
    e1.sm.add_executing("bridge")
    d1 = CallExpansion(spawner_id="bridge", record={"payload": "go"}, children=[])
    e1.expansions.append(d1)
    e1._grow_call("bridge", bridge1.child, {"payload": "go"}, d1, schedule=False)
    ids1 = {n for n in e1.flow.nodes if n.startswith("bridge/")}

    g2 = call_with_inner_pause()
    e2 = FlowEngine(g2)
    bridge2 = e2.flow.nodes["bridge"]
    e2.sm.add_executing("bridge")
    d2 = CallExpansion(spawner_id="bridge", record={"payload": "go"}, children=[])
    e2.expansions.append(d2)
    e2._grow_call("bridge", bridge2.child, {"payload": "go"}, d2, schedule=False)
    ids2 = {n for n in e2.flow.nodes if n.startswith("bridge/")}

    assert ids1 == ids2 and ids1  # identical, non-empty
    assert e1.alias["bridge/__end__"] == "bridge"
    assert e1.sm.node_state["bridge"] == NodeState.EXPANDED


# --- nested fixtures: CALL-in-CALL — the recursion the flat fixtures can't probe -------- #


def call_in_call_with_inner_pause():
    """Parent -> outer(CALL deep_child) -> after; deep_child -> deep(CALL inner_pause_child)
    -> END_ID. A CALL whose child contains a CALL whose child pauses — two nesting levels, so a
    dropped recursion / mis-stamped depth shows up (a flat CALL would not)."""
    inner = _inner_pause_child()
    deep = CallNode("deep", flow_id="inner_pause_child", child=inner,
                    child_inputs=inner.nodes[inner.start_id].params)
    stamp_reads(deep, {"payload": "${input.payload}"})
    deep_child = _graph(
        [deep],
        [(START_ID, "deep"), ("deep", END_ID)],
        outputs=[FlowOutput(name="answer", from_="${deep.output.answer}")],
    )
    outer = CallNode("outer", flow_id="deep_child", child=deep_child,
                     child_inputs=deep_child.nodes[deep_child.start_id].params)
    stamp_reads(outer, {"payload": "${input.payload}"})
    after = FuncNode("after", lambda p: {"output": p["v"]})
    stamp_reads(after, {"v": "${outer.output.answer}"})
    return _graph(
        [outer, after],
        [(START_ID, "outer"), ("outer", "after"), ("after", END_ID)],
        outputs=[FlowOutput(name="r", from_="${after.output}")],
    )


def _capture_overlay(engine):
    """The four overlay maps + the registered topology a replay must reproduce."""
    return {
        "nodes": set(engine.flow.nodes),
        "alias": dict(engine.alias),
        "depth": dict(engine.depth),
        "spawner_keys": set(engine._spawner_expansion),
        "edge_state_keys": set(engine.sm.edge_state),
    }


def test_replay_reproduces_live_overlay_nested_oracle():
    """Replaying `ckpt.expansions` onto a FRESH recompiled flow rebuilds the SAME
    flow.nodes / alias / depth / _spawner_expansion keys / edge_state (set-equality) the
    live engine grew. Uses a NESTED (CALL-in-CALL) fixture — a flat fixture is blind to a
    dropped recursion."""
    live = FlowEngine(call_in_call_with_inner_pause(), run_inputs={"payload": "go"})
    assert isinstance(list(live.run())[-1], RunPaused)
    live_overlay = _capture_overlay(live)
    ckpt = RunCheckpoint.loads(live.snapshot().dumps())

    # Replay in isolation (before restore() wires it in): a bare fresh engine on a
    # recompiled flow + _replay_expansions reproduces the overlay.
    fresh = FlowEngine(call_in_call_with_inner_pause())
    fresh._replay_expansions(ckpt.expansions)
    replayed = _capture_overlay(fresh)

    assert replayed["nodes"] == live_overlay["nodes"]
    assert replayed["alias"] == live_overlay["alias"]
    assert replayed["depth"] == live_overlay["depth"]
    assert replayed["spawner_keys"] == live_overlay["spawner_keys"]
    assert replayed["edge_state_keys"] == live_overlay["edge_state_keys"]


# --- restore() re-seed + clean-flow guard (the pure-static deferred case) ---------------- #


def _fork_with_deferred():
    """start -> {ask(HUMAN_INPUT), b1 -> b2} -> join -> END_ID. While `ask` is parked, the b1->b2
    branch runs b1 and b2 becomes ready DURING suspension -> held in `deferred`. Pure-static
    (no expansion), so it isolates the restore re-seed half (paused + deferred + ready)."""
    def _join(i):
        return {"output": [i["a"], i["b"]]}

    join = FuncNode("join", _join)
    stamp_reads(join, {"a": "${ask.output}", "b": "${b2.output}"})
    b1 = FuncNode("b1", lambda p: {"output": "b1"})
    b2 = FuncNode("b2", lambda p: {"output": p["v"]})
    stamp_reads(b2, {"v": "${b1.output}"})
    return _graph(
        [FuncNode("start", lambda p: {"output": "go"}),
         HumanInputNode("ask", prompt="ok?"), b1, b2, join],
        [(START_ID, "start"), ("start", "ask"), ("start", "b1"),
         ("b1", "b2"), ("ask", "join"), ("b2", "join"), ("join", END_ID)],
        outputs=[FlowOutput(name="r", from_="${join.output.output}")],
    )


def test_restore_static_fork_with_deferred_matches_live():
    """A fork whose un-parked branch deferred a node. A durable
    dumps->loads->restore(fresh)->resume(Deliver ask) reaches the SAME terminal as the live
    resume (RunSucceeded, both branches present)."""
    live = FlowEngine(_fork_with_deferred())
    assert isinstance(list(live.run())[-1], RunPaused)
    ckpt = live.snapshot()
    assert ckpt.deferred_nodes == ["b2"]        # b2 became ready while suspending
    assert ckpt.ready == []                      # the live serial pause fully drains ready
    live_out = list(live.resume(
        commands=[DeliverAnswerCommand(node_id="ask", value="A")]))[-1]
    assert isinstance(live_out, RunSucceeded)
    assert live_out.output[0] == "A"            # the delivered ask answer (both branches present)

    back = RunCheckpoint.loads(ckpt.dumps())
    fresh = FlowEngine.restore(_fork_with_deferred(), back)
    assert [n for n, _ in fresh.paused] == ["ask"]
    assert fresh.deferred == ["b2"]
    dur_out = list(fresh.resume(
        commands=[DeliverAnswerCommand(node_id="ask", value="A")]))[-1]
    assert isinstance(dur_out, RunSucceeded)
    assert dur_out.output == live_out.output    # durable restore(fresh)+resume == live


def test_restore_on_grown_flow_raises():
    """restore() requires a CLEAN flow. A flow already carrying namespaced/cloned ids (a
    re-grown one) raises ValueError BEFORE replay (add_subgraph is non-idempotent)."""
    import pytest

    live = FlowEngine(call_with_inner_pause(), run_inputs={"payload": "go"})
    assert isinstance(list(live.run())[-1], RunPaused)
    ckpt = RunCheckpoint.loads(live.snapshot().dumps())
    with pytest.raises(ValueError, match="requires a clean flow"):
        FlowEngine.restore(live.flow, ckpt)     # live.flow is already grown (has bridge/... ids)


# --- durable e2e over GROWN graphs on FRESHLY recompiled flows -------------------------- #


def _durable_resume(make_flow, run_inputs, answer):
    """Run `make_flow()` to a pause, snapshot->dumps->loads->restore on a SECOND freshly
    built flow (true cross-process), deliver `answer` to the parked leaf, drive to terminal.
    Returns (live_terminal, durable_terminal, restored_engine)."""
    live = FlowEngine(make_flow(), run_inputs=run_inputs)
    live_evs = list(live.run())
    assert isinstance(live_evs[-1], RunPaused)
    parked = live.snapshot().paused_nodes
    live_term = list(live.resume(
        commands=[DeliverAnswerCommand(node_id=p, value=answer) for p in parked]))[-1]

    # restart fresh: a true second process re-runs to the SAME pause, persists, restores.
    proc1 = FlowEngine(make_flow(), run_inputs=run_inputs)
    assert isinstance(list(proc1.run())[-1], RunPaused)
    ckpt = RunCheckpoint.loads(proc1.snapshot().dumps())
    fresh = FlowEngine.restore(make_flow(), ckpt)
    parked2 = ckpt.paused_nodes
    dur_term = list(fresh.resume(
        commands=[DeliverAnswerCommand(node_id=p, value=answer) for p in parked2]))[-1]
    return live_term, dur_term, fresh


def test_durable_call_inner_pause_resumes_on_fresh_flow():
    """A CALL grown to an inner pause resumes cross-process on a freshly recompiled flow
    -> RunSucceeded matching live. The delivered answer flows through the cloned child and the
    CALL substitutes it under the spawner id (pool['bridge']=='approve')."""
    live_term, dur_term, fresh = _durable_resume(
        call_with_inner_pause, {"payload": "go"}, "approve")
    assert isinstance(dur_term, RunSucceeded)
    assert dur_term.output == live_term.output            # durable == live (same terminal)
    assert fresh.pool.get("bridge") == "approve"          # the answer propagated through the CALL


def test_durable_map_inner_pause_resumes_on_fresh_flow():
    """A MAP grown to per-element inner pauses resumes cross-process -> RunSucceeded list,
    element order preserved. Asserts the map_end fan-in node was rebuilt + aliased."""
    live_term, dur_term, fresh = _durable_resume(
        map_with_inner_pause, {"items": ["a", "b"]}, "ok")
    assert isinstance(dur_term, RunSucceeded)
    assert dur_term.output == live_term.output
    assert dur_term.output == ["ok", "ok"]                  # one per element, order preserved
    assert "each/__end__" in fresh.flow.nodes               # the LIST fan-in node
    assert fresh.alias["each/__end__"] == "each"


def _map_n0_via_sibling():
    """start -> {empty(MAP over []), ask(HUMAN_INPUT)} -> join -> END_ID. The MAP fires with N=0
    (emits []) while `ask` parks -> at pause the MapExpansion carries records==[] (N=0)."""
    child = _inner_pause_child()
    empty = MapNode("empty", flow_id="inner_pause_child", child=child,
                    child_inputs=child.nodes[child.start_id].params)
    stamp_reads(empty, {"over": "${input.items}", "payload": "${item}"})
    join = FuncNode("join", lambda i: {"output": [i["m"], i["a"]]})
    stamp_reads(join, {"m": "${empty.output}", "a": "${ask.output}"})
    return _graph(
        [empty, HumanInputNode("ask", prompt="ok?"), join],
        [(START_ID, "empty"), (START_ID, "ask"), ("empty", "join"), ("ask", "join"), ("join", END_ID)],
        outputs=[FlowOutput(name="r", from_="${join.output.output}")],
    )


def test_durable_map_n0_via_sibling_resumes_on_fresh_flow():
    """A MAP over [] that fired before a sibling pause restores + resumes cross-process.
    The replay rebuilds EndNode.list_(n=0) (a 0-incoming root that emits [])."""
    from agent_composer.suspension.expansions import MapExpansion

    proc1 = FlowEngine(_map_n0_via_sibling(), run_inputs={"items": []})
    assert isinstance(list(proc1.run())[-1], RunPaused)
    ckpt = RunCheckpoint.loads(proc1.snapshot().dumps())
    maps = [e for e in ckpt.expansions if isinstance(e, MapExpansion)]
    assert len(maps) == 1 and maps[0].records == []        # N=0 descriptor persisted

    fresh = FlowEngine.restore(_map_n0_via_sibling(), ckpt)
    assert "empty/__end__" in fresh.flow.nodes              # N=0 fan-in node rebuilt
    term = list(fresh.resume(
        commands=[DeliverAnswerCommand(node_id=ckpt.paused_nodes[0], value="A")]))[-1]
    assert isinstance(term, RunSucceeded)
    assert term.output == [[], "A"]                         # MAP over [] -> [], sibling -> "A"


# --- AGENT durable resume + the 2-hop ledger regression --------------------------------- #


def _two_pause_agent_chat():
    """A 2-pause mock chat (the test_agent_continuation pattern): two ask_user tool calls,
    then a FINAL answer."""
    from langchain_core.messages import AIMessage
    from tests.engine.test_agent_continuation import _ask
    return [_ask({"question": "q1?"}, "q1"), _ask({"question": "q2?"}, "q2"),
            AIMessage(content="FINAL")]


def test_durable_two_pause_agent_resumes_on_fresh_flow(monkeypatch):
    """A 2-pause AGENT restored at pause 1 onto a freshly recompiled flow resumes past BOTH
    pauses -> RunSucceeded 'FINAL'. The segment-2 leaf is deeply namespaced under the
    segment-1 resume id, and exists in the restored flow after the live segment-2 growth on
    the restored engine.

    ONE shared mock chat across the simulated processes: the carried memo replays prior turns
    WITHOUT re-invoking the model, so the 3 replies [q1, q2, FINAL] are consumed exactly once
    each even across restore (mirrors test_agent_continuation's single-chat invariant)."""
    import agent_composer.llm_clients as llm
    from agent_composer import load_flow
    from agent_composer.compose.run import resume_command, run_flow
    from tests.engine.test_agent_continuation import _chat, ASK

    chat = _chat(_two_pause_agent_chat())                   # ONE instance shared across processes
    monkeypatch.setattr(llm, "model_from_config", lambda cfg: chat)
    loaded = load_flow(ASK)
    rec = run_flow(loaded, {})                              # parks at pause 1 (invoke #1 -> q1)
    assert rec.status == "paused"
    ckpt = RunCheckpoint.loads(rec.checkpoint.dumps())

    fresh = FlowEngine.restore(load_flow(ASK).compiled, ckpt)
    # resume past pause 1 -> the restored engine grows segment 2 and parks at pause 2 (invoke #2).
    evs1 = list(fresh.resume(commands=[resume_command(loaded, ckpt.pause_reasons[0], "a1")]))
    paused2 = [e for e in evs1 if isinstance(e, RunPaused)]
    assert paused2, "segment-2 pause must surface on the restored engine"
    seg2_leaf = paused2[-1].reasons[0].node_id
    assert seg2_leaf.count("/") >= 2 and seg2_leaf in fresh.flow.nodes  # deeply namespaced
    # deliver pause 2 -> FINAL (invoke #3)
    evs2 = list(fresh.resume(commands=[resume_command(loaded, paused2[-1].reasons[0], "a2")]))
    assert isinstance(evs2[-1], RunSucceeded) and evs2[-1].output == "FINAL"


def test_two_hop_agent_resnapshot_ledger_matches_live(monkeypatch):
    """run->snapshot->restore(fresh)->resume-to-2nd-pause->RE-snapshot. The re-snapshot's
    expansions tree must equal the live engine's: ONE AgentExpansion with 2 segments (NOT two
    top-level AgentExpansions / a truncated 1-segment tree). Then a 3rd process
    restore(fresh)->resume reaches RunSucceeded 'FINAL'."""
    import agent_composer.llm_clients as llm
    from agent_composer import load_flow
    from agent_composer.compose.run import resume_command, run_flow
    from agent_composer.suspension.expansions import AgentExpansion
    from tests.engine.test_agent_continuation import _chat, ASK

    # --- live oracle: run to pause 2 on ONE engine; capture its ledger shape ---
    # Its OWN chat (consumes its own 3 replies independently of the durable sequence).
    monkeypatch.setattr(llm, "model_from_config", lambda cfg: _chat(_two_pause_agent_chat()))
    loaded = load_flow(ASK)
    live = run_flow(loaded, {})                             # pause 1
    list(live.engine.resume(commands=[resume_command(loaded, live.pause_reasons[0], "a1")]))  # pause 2
    live_tree = live.engine.snapshot().expansions
    assert len(live_tree) == 1 and isinstance(live_tree[0], AgentExpansion)
    assert len(live_tree[0].segments) == 2                 # ONE AgentExpansion, TWO segments

    # --- the durable sequence: ONE shared chat across the 3 simulated processes (the memo
    # replays prior turns without re-invoking, so [q1, q2, FINAL] is consumed once each) ---
    chat = _chat(_two_pause_agent_chat())
    monkeypatch.setattr(llm, "model_from_config", lambda cfg: chat)
    proc1 = run_flow(load_flow(ASK), {})                   # pause 1 (invoke #1)
    ckpt1 = RunCheckpoint.loads(proc1.checkpoint.dumps())
    hop1 = FlowEngine.restore(load_flow(ASK).compiled, ckpt1)
    list(hop1.resume(commands=[resume_command(loaded, ckpt1.pause_reasons[0], "a1")]))  # pause 2 (invoke #2)
    hop1_tree = hop1.snapshot().expansions
    # the re-snapshot after a durable hop is the FULL tree, not a truncated/duplicated one.
    assert len(hop1_tree) == 1 and isinstance(hop1_tree[0], AgentExpansion)
    assert len(hop1_tree[0].segments) == 2

    # --- hop 2: a 3rd process restores the re-snapshot and finishes ---
    ckpt2 = RunCheckpoint.loads(hop1.snapshot().dumps())
    hop2 = FlowEngine.restore(load_flow(ASK).compiled, ckpt2)
    evs = list(hop2.resume(commands=[resume_command(loaded, ckpt2.pause_reasons[0], "a2")]))  # invoke #3
    assert isinstance(evs[-1], RunSucceeded) and evs[-1].output == "FINAL"


# --- NESTED durable resume — CALL-in-CALL + MAP-of-CALL --------------------------------- #


def test_durable_call_in_call_resumes_on_fresh_flow():
    """A CALL-in-CALL grown to a deep inner pause restores + resumes cross-process on a
    freshly recompiled flow. Asserts the doubly-namespaced leaf, the depth tree, and the
    nested-spawner parent-pointer were all rebuilt by the recursion."""
    proc1 = FlowEngine(call_in_call_with_inner_pause(), run_inputs={"payload": "go"})
    assert isinstance(list(proc1.run())[-1], RunPaused)
    ckpt = RunCheckpoint.loads(proc1.snapshot().dumps())
    live_term = list(proc1.resume(
        commands=[DeliverAnswerCommand(node_id=ckpt.paused_nodes[0], value="A")]))[-1]

    fresh = FlowEngine.restore(call_in_call_with_inner_pause(), ckpt)
    assert "outer/deep/ask" in fresh.flow.nodes                 # doubly namespaced
    assert fresh.depth["outer/deep"] == 1
    assert fresh.depth["outer/__end__"] == 1
    assert fresh.depth["outer/deep/__end__"] == 2
    assert "outer/deep" in fresh._spawner_expansion            # nested spawner parent-pointer
    dur_term = list(fresh.resume(
        commands=[DeliverAnswerCommand(node_id=ckpt.paused_nodes[0], value="A")]))[-1]
    assert isinstance(dur_term, RunSucceeded)
    assert dur_term.output == live_term.output                 # durable == live


def _map_of_call_with_inner_pause():
    """A MAP whose child contains a CALL whose child pauses (the in-repo
    test_nested_call_inside_map shape): the inner CallExpansion rides under
    map_desc.children_per_element[i] — recursion through a MAP element."""
    inner_child = _inner_pause_child()
    nested_call = CallNode("inner_bridge", flow_id="inner_pause_child", child=inner_child,
                           child_inputs=inner_child.nodes[inner_child.start_id].params)
    stamp_reads(nested_call, {"payload": "${input.payload}"})
    parent_child = _graph(
        [nested_call],
        [(START_ID, "inner_bridge"), ("inner_bridge", END_ID)],
        outputs=[FlowOutput(name="result", from_="${inner_bridge.output.answer}")],
    )
    each = MapNode("each", flow_id="parent_child", child=parent_child,
                   child_inputs=parent_child.nodes[parent_child.start_id].params)
    stamp_reads(each, {"over": "${input.items}", "payload": "${item}"})
    return _graph(
        [each],
        [(START_ID, "each"), ("each", END_ID)],
        outputs=[FlowOutput(name="r", from_="${each.output}")],
    )


def test_durable_map_of_call_resumes_on_fresh_flow():
    """A MAP-of-CALL grown to a deep per-element pause restores + resumes cross-process. The
    nested CallExpansion lives under children_per_element[0]; the replay recurses into it and
    rebuilds the doubly-namespaced clone."""
    from agent_composer.suspension.expansions import CallExpansion, MapExpansion

    proc1 = FlowEngine(_map_of_call_with_inner_pause(), run_inputs={"items": ["a"]})
    assert isinstance(list(proc1.run())[-1], RunPaused)
    ckpt = RunCheckpoint.loads(proc1.snapshot().dumps())
    top_maps = [e for e in ckpt.expansions if isinstance(e, MapExpansion)]
    assert len(top_maps) == 1
    assert isinstance(top_maps[0].children_per_element[0][0], CallExpansion)  # nested under elem 0
    live_term = list(proc1.resume(
        commands=[DeliverAnswerCommand(node_id=ckpt.paused_nodes[0], value="A")]))[-1]

    fresh = FlowEngine.restore(_map_of_call_with_inner_pause(), ckpt)
    assert any(n.startswith("each#0/inner_bridge/") for n in fresh.flow.nodes)  # doubly namespaced
    dur_term = list(fresh.resume(
        commands=[DeliverAnswerCommand(node_id=ckpt.paused_nodes[0], value="A")]))[-1]
    assert isinstance(dur_term, RunSucceeded)
    assert dur_term.output == live_term.output                 # durable == live


# --- AGENT(ask_user) nested under a CALL — durable resume ------------------------------- #
# The AGENT durable tests cover a TOP-LEVEL agent, and the CALL/MAP durable tests use a
# HUMAN_INPUT child — but nothing else exercises an AGENT pause whose AgentExpansion rides
# UNDER a CallExpansion (the `parent_desc is not None` AGENT arm of `_apply_enqueue`, the
# `_append_child` path). These drive that nesting cross-process.

_CALL_WRAPS_AGENT = """
id: cag
name: cag
defs:
  approver:
    nodes:
      agent: {kind: agent, prompt: go, controls: [ask_user], output: str}
    output: ${agent.output}
nodes:
  gate:
    kind: call
    call: approver
output: ${gate.output}
"""


def test_durable_agent_under_call_resumes_on_fresh_flow(monkeypatch):
    """An AGENT(ask_user) inside a CALL child pauses ONCE; its AgentExpansion rides UNDER the
    gate CallExpansion (the `parent_desc is not None` AGENT arm). A cross-process
    dumps->loads->restore(fresh)->resume drives past the pause to RunSucceeded 'FINAL'. The
    parked leaf is deeply namespaced under BOTH the call AND the agent spawner."""
    import agent_composer.llm_clients as llm
    from langchain_core.messages import AIMessage

    from agent_composer import load_flow
    from agent_composer.compose.run import resume_command, run_flow
    from agent_composer.suspension.expansions import AgentExpansion, CallExpansion
    from tests.engine.test_agent_continuation import _ask, _chat

    chat = _chat([_ask({"question": "ok?"}, "q1"), AIMessage(content="FINAL")])  # ONE shared chat
    monkeypatch.setattr(llm, "model_from_config", lambda cfg: chat)
    loaded = load_flow(_CALL_WRAPS_AGENT)
    rec = run_flow(loaded, {})                                  # invoke #1 -> ask_user -> pause
    assert rec.status == "paused"
    reason = rec.pause_reasons[0]
    assert reason.node_id == "gate/agent/__ask#q1"             # deep: call ns + agent spawner ns
    assert reason.node_id in rec.engine.flow.nodes

    # the descriptor tree: ONE CallExpansion whose child is the AgentExpansion (nesting)
    ckpt = RunCheckpoint.loads(rec.checkpoint.dumps())
    calls = [e for e in ckpt.expansions if isinstance(e, CallExpansion)]
    assert len(calls) == 1
    assert [type(c).__name__ for c in calls[0].children] == ["AgentExpansion"]
    assert isinstance(calls[0].children[0], AgentExpansion)

    fresh = FlowEngine.restore(load_flow(_CALL_WRAPS_AGENT).compiled, ckpt)
    assert reason.node_id in fresh.flow.nodes                  # the deep leaf rebuilt by replay
    evs = list(fresh.resume(commands=[resume_command(loaded, ckpt.pause_reasons[0], "yes")]))
    assert isinstance(evs[-1], RunSucceeded) and evs[-1].output == "FINAL"


def test_durable_two_pause_agent_under_call_resumes_on_fresh_flow(monkeypatch):
    """The multi-pause variant — an AGENT inside a CALL pauses TWICE. Restored at pause 1 onto
    a freshly recompiled flow, the resume grows segment 2 (still under the gate CallExpansion)
    and parks at pause 2; delivering it reaches RunSucceeded 'FINAL'. The segment-2 leaf chains
    under the segment-1 resume id AND the call namespace (triply deep)."""
    import agent_composer.llm_clients as llm
    from langchain_core.messages import AIMessage

    from agent_composer import load_flow
    from agent_composer.compose.run import resume_command, run_flow
    from agent_composer.suspension.expansions import AgentExpansion, CallExpansion
    from tests.engine.test_agent_continuation import _ask, _chat

    chat = _chat([_ask({"question": "q1?"}, "q1"), _ask({"question": "q2?"}, "q2"),
                  AIMessage(content="FINAL")])                  # ONE shared chat across processes
    monkeypatch.setattr(llm, "model_from_config", lambda cfg: chat)
    loaded = load_flow(_CALL_WRAPS_AGENT)
    rec = run_flow(loaded, {})                                  # pause 1 (invoke #1)
    assert rec.status == "paused"
    ckpt = RunCheckpoint.loads(rec.checkpoint.dumps())

    fresh = FlowEngine.restore(load_flow(_CALL_WRAPS_AGENT).compiled, ckpt)
    # resume past pause 1 -> the restored engine grows segment 2 and parks at pause 2 (invoke #2).
    evs1 = list(fresh.resume(commands=[resume_command(loaded, ckpt.pause_reasons[0], "a1")]))
    paused2 = [e for e in evs1 if isinstance(e, RunPaused)]
    assert paused2, "segment-2 pause must surface on the restored engine"
    seg2_leaf = paused2[-1].reasons[0].node_id
    assert seg2_leaf == "gate/agent/__resume#q1/__ask#q2"      # call ns + agent continuation chain
    assert seg2_leaf in fresh.flow.nodes

    # the re-snapshot ledger is still ONE CallExpansion -> ONE AgentExpansion with TWO segments.
    tree = fresh.snapshot().expansions
    calls = [e for e in tree if isinstance(e, CallExpansion)]
    assert len(calls) == 1 and len(calls[0].children) == 1
    agent_desc = calls[0].children[0]
    assert isinstance(agent_desc, AgentExpansion) and len(agent_desc.segments) == 2

    # deliver pause 2 -> FINAL (invoke #3)
    evs2 = list(fresh.resume(commands=[resume_command(loaded, paused2[-1].reasons[0], "a2")]))
    assert isinstance(evs2[-1], RunSucceeded) and evs2[-1].output == "FINAL"


# --- review fixes ----------------------------------------------------------- #


def _side_counter_flow(runs):
    # start -> ask(HUMAN_INPUT) -> END_ID ; plus a `side` counter rooted off START_ID (dead-end leaf)
    return _graph(
        [FuncNode("start", lambda p: {"output": "go"}),
         HumanInputNode("ask", prompt="?"),
         FuncNode("side", lambda p: runs.append(1) or {"output": "s"})],
        [(START_ID, "start"), ("start", "ask"), ("ask", END_ID), (START_ID, "side"), ("side", END_ID)],
        outputs=[FlowOutput(name="r", from_="${ask.output}"), FlowOutput(name="s", from_="${side.output}")],
    )


def test_resume_clears_ready_so_a_queued_node_runs_once():
    """restore() re-seeds `self.ready` from the checkpoint; resume() must clear it before
    re-enqueuing the seed, else a queued id runs TWICE. No in-system path produces a non-empty
    `checkpoint.ready` (serial drain empties it), so this FORGES one to pin the invariant the
    re-seed introduced."""
    runs: list = []
    e1 = FlowEngine(_side_counter_flow(runs))
    assert isinstance(list(e1.run())[-1], RunPaused)          # `side` runs once here, parks at ask
    ck = e1.snapshot()
    # forge: pretend `side` was queued-but-not-run at the pause (the dormant re-seed path)
    ck = ck.model_copy(update={
        "ready": ["side"],
        "node_state": {**ck.node_state, "side": NodeState.UNKNOWN},
    })
    runs.clear()
    e2 = FlowEngine.restore(_side_counter_flow(runs), ck)
    assert list(e2.ready) == ["side"]                          # re-seeded from the checkpoint
    list(e2.resume(commands=[DeliverAnswerCommand(node_id="ask", value="a")]))
    assert runs == [1]                                         # ran EXACTLY once (without the fix: [1, 1])


def test_restore_does_not_mutate_a_held_checkpoint():
    """restore() deep-copies the pool + expansion descriptors (symmetric with snapshot()'s
    write-side copy), so resuming a restored engine does not retro-mutate a checkpoint object
    the host still holds (reachable via the public resume_flow(checkpoint=))."""
    proc1 = FlowEngine(call_with_inner_pause(), run_inputs={"payload": "go"})
    assert isinstance(list(proc1.run())[-1], RunPaused)
    held = proc1.snapshot()                                    # a retained object (no loads())
    before_keys = set(held.pool.store.keys())
    e2 = FlowEngine.restore(call_with_inner_pause(), held)
    assert held.pool is not e2.pool                            # deep-copied, not aliased
    assert held.expansions[0] is not e2.expansions[0]
    list(e2.resume(commands=[DeliverAnswerCommand(node_id=e2.paused[0][0], value="approve")]))
    assert set(held.pool.store.keys()) == before_keys          # the held checkpoint is untouched


def test_replay_does_not_promote_a_top_level_agent_segment_child(monkeypatch):
    """Forward-proofing: a (future) child of a TOP-LEVEL AgentExpansion segment must NOT be
    promoted to a top-level ledger entry. `AgentSegment.children` is always [] today; this pins
    the `is_top_level` gate (vs the old `parent_depth == 0`, which an AGENT — depth unchanged —
    would wrongly re-trip) against the reserved slot."""
    from types import SimpleNamespace
    from agent_composer.suspension.expansions import AgentExpansion, AgentSegment

    inner = AgentExpansion(spawner_id="inner",
                           segments=[AgentSegment(hi_desc={}, resume_desc={})])
    top = AgentExpansion(
        spawner_id="agent",
        segments=[AgentSegment(hi_desc={}, resume_desc={}, children=[inner])],
    )
    e = FlowEngine(_side_counter_flow([]))
    # stub the grow helper so the fold reaches the children recursion without cloning
    monkeypatch.setattr(e, "_grow_agent_segment",
                        lambda *a, **k: SimpleNamespace(out_node_id="agent/__resume#q"))
    e.expansions = []
    e._replay_expansions([top])
    assert e.expansions == [top]                               # NOT [top, inner]


def test_grow_loop_schedule_false_registers_without_scheduling():
    """`_grow_loop(..., schedule=False)` rebuilds the iteration overlay (clones + registers the
    `#0/` namespace, records the seed on the LoopExpansion) but schedules NOTHING and does not
    trip the node-budget guard — the suppressed replay path, mirroring `_grow_call`/`_grow_map`."""
    from agent_composer.compose.loader import load_flow
    from agent_composer.suspension.expansions import LoopExpansion
    from tests.engine.test_loop_run import COUNTER

    engine = FlowEngine(load_flow(COUNTER).compiled, run_inputs={})
    spawner_id = "loop"
    child = engine.flow.nodes[spawner_id].child
    record = {"n": 0, "exited": False}
    desc = LoopExpansion(spawner_id=spawner_id, records=[], children_per_iter=[])

    engine._grow_loop(spawner_id, child, record, 0, desc, schedule=False)

    # Registered: the iteration's namespaced nodes are present and the seed is on the ledger.
    assert any(n.startswith(f"{spawner_id}#0/") for n in engine.flow.nodes)
    assert len(desc.records) == 1

    # Scheduled: nothing — the serial ready queue is empty right after the suppressed grow.
    assert list(engine.ready) == []


def test_replay_reproduces_live_loop_overlay():
    """Replaying a paused-loop `ckpt.expansions` onto a FRESH recompiled flow rebuilds the SAME
    live iteration overlay the live engine grew: flow.nodes / loop_alias / sm.node_state
    (set-equality), the spawner staying EXPANDED, and the ledger re-attached to `loop_desc`.

    Pruning drops committed iterations before any pause, so exactly ONE iteration (the LIVE one)
    is resident — the replay re-grows that single last-recorded seed at its recorded index."""
    from agent_composer.compose.loader import load_flow
    from agent_composer.compose.run import run_flow
    from agent_composer.suspension.expansions import LoopExpansion
    from tests.engine.test_loop_run import LOOP_CHAT

    # Drive LOOP_CHAT to its FIRST pause on a live engine: iteration #0 is grown (NOT yet pruned
    # — pruning happens in _loop_step AFTER the body END fires, but the body parks BEFORE its END).
    loaded = load_flow(LOOP_CHAT)
    live = run_flow(loaded, {})
    assert live.status == "paused"
    live_engine = live.engine

    # Precondition: the live overlay has the `loop#0/` namespace present (un-pruned live iter).
    assert any(n.startswith("loop#0/") for n in live_engine.flow.nodes)

    # Capture the loop-relevant overlay a replay must reproduce (loop_alias, not alias, holds the
    # body-END filler; also capture sm.node_state and the spawner's own EXPANDED state).
    live_nodes = set(live_engine.flow.nodes)
    live_loop_alias = dict(live_engine.loop_alias)
    live_node_state = set(live_engine.sm.node_state)
    assert live_engine.sm.node_state["loop"] == NodeState.EXPANDED

    # Snapshot + round-trip: exactly ONE LoopExpansion with a non-empty records list (live seed).
    ckpt = RunCheckpoint.loads(live_engine.snapshot().dumps())
    loop_descs = [e for e in ckpt.expansions if isinstance(e, LoopExpansion)]
    assert len(loop_descs) == 1
    assert loop_descs[0].records                       # the live iteration seed is recorded

    # Replay in isolation onto a FRESH recompiled flow (before restore() wires it in).
    fresh = FlowEngine(load_flow(LOOP_CHAT).compiled)
    fresh._replay_expansions(ckpt.expansions)

    # Set-for-set overlay equality against the live engine.
    assert set(fresh.flow.nodes) == live_nodes
    assert dict(fresh.loop_alias) == live_loop_alias
    assert set(fresh.sm.node_state) == live_node_state
    assert fresh.sm.node_state["loop"] == NodeState.EXPANDED
    assert fresh.loop_desc["loop"] is loop_descs[0]    # the ledger was re-attached


def test_durable_loop_resumes_on_fresh_flow():
    """A `loop` paused mid-body resumes cross-process on a freshly recompiled flow, driven
    through MULTIPLE turns (with a durable hop across a mid-loop pause), reaching the SAME
    terminal as the live in-process resume.

    Pruning drops committed iterations before any pause, so at the turn-1 pause only the LIVE
    `loop#0/` iteration is resident. The checkpoint carries ONE LoopExpansion with the live
    seed recorded; restore()+replay re-grows exactly that iteration on a clean flow. Delivering
    "hi" then drives the loop-back (`_loop_step`) which must grow turn 2 and prune turn 1 from
    the RESTORED overlay — the post-restore `loop_iter`/`loop_desc` must be consistent for that
    to compute the next index and read the predicate. Delivering "bye" sets exited=true ->
    predicate false -> the run succeeds identically to the live oracle."""
    from agent_composer.compose.loader import load_flow
    from agent_composer.compose.run import resume_command, resume_flow, run_flow
    from agent_composer.suspension.expansions import LoopExpansion
    from tests.engine.test_loop_run import LOOP_CHAT

    # --- Live oracle (in-process): drive LOOP_CHAT to terminal exactly as the in-process test.
    loaded = load_flow(LOOP_CHAT)
    live1 = run_flow(loaded, {})
    assert live1.status == "paused"
    live2 = resume_flow(loaded, engine=live1.engine,
                        commands=[resume_command(loaded, live1.pause_reasons[0], "hi")])
    assert live2.status == "paused"
    live3 = resume_flow(loaded, engine=live2.engine,
                        commands=[resume_command(loaded, live2.pause_reasons[0], "bye")])
    assert live3.status == "succeeded", live3.error
    live_output = live3.output
    assert live_output == {"messages": ["hi", "bye"], "exited": True}

    # --- Durable sequence: a fresh process parks at the turn-1 pause, persists, round-trips.
    proc1 = run_flow(load_flow(LOOP_CHAT), {})
    assert proc1.status == "paused"
    ckpt = RunCheckpoint.loads(proc1.checkpoint.dumps())   # cross-process round-trip

    # Exactly ONE LoopExpansion, with the live iteration's seed recorded (non-empty).
    loop_descs = [e for e in ckpt.expansions if isinstance(e, LoopExpansion)]
    assert len(loop_descs) == 1
    assert loop_descs[0].records                            # the live #0 seed is recorded

    # Pruning leaves only the live iteration: node_state carries `loop#0/` keys but no `loop#1/`.
    assert any(k.startswith("loop#0/") for k in ckpt.node_state)
    assert not any(k.startswith("loop#1/") for k in ckpt.node_state)

    # --- Restore on a FRESH recompiled flow and drive the remaining turns to terminal.
    fresh = FlowEngine.restore(load_flow(LOOP_CHAT).compiled, ckpt)

    # The replay rebuilt the live iteration's namespaced human_input leaf on the fresh flow.
    parked_leaf = ckpt.pause_reasons[0].node_id
    assert parked_leaf in fresh.flow.nodes

    # Deliver "hi" to the restored turn-1 pause -> the loop-back grows turn 2 -> a NEW pause.
    dur2 = resume_flow(loaded, engine=fresh,
                       commands=[resume_command(loaded, ckpt.pause_reasons[0], "hi")])
    assert dur2.status == "paused", dur2.error
    # Deliver "bye" -> fold sets exited=true -> predicate false -> succeeds.
    dur3 = resume_flow(loaded, engine=dur2.engine,
                       commands=[resume_command(loaded, dur2.pause_reasons[0], "bye")])
    assert dur3.status == "succeeded", dur3.error
    assert dur3.output == live_output                       # durable == live (same terminal)


def test_snapshot_captures_num_workers():
    from agent_composer.runtime.engine import FlowEngine
    from tests.engine.test_engine_expansions_ledger import call_with_inner_pause
    eng = FlowEngine(call_with_inner_pause(), num_workers=3)
    assert eng.snapshot().num_workers == 3


def test_restore_defaults_to_checkpointed_count_and_override():
    """restore() rebuilds at the checkpoint's num_workers; an explicit kwarg overrides."""
    from agent_composer.runtime.engine import FlowEngine
    from tests.engine.test_engine_expansions_ledger import call_with_inner_pause
    src = FlowEngine(call_with_inner_pause(), num_workers=2)
    ckpt = src.snapshot()
    # fresh clean flow per restore (restore mutates flow in place; replay needs a clean graph)
    e_default = FlowEngine.restore(call_with_inner_pause(), ckpt)
    assert e_default.num_workers == 2
    e_override = FlowEngine.restore(call_with_inner_pause(), ckpt, num_workers=0)
    assert e_override.num_workers == 0


def test_durable_resume_pooled_matches_serial():
    """dumps -> loads -> restore(num_workers=N) -> resume reaches the same terminal as a
    serial durable resume. A run checkpointed serial is resumable pooled (override) and
    vice-versa — correctness is worker-count-independent."""
    from agent_composer.compose.run import run_flow, resume_flow
    from agent_composer.compose.loader import load_flow
    from tests.engine.test_run_resume import _RESUME_FANOUT

    loaded = load_flow(_RESUME_FANOUT)
    paused = run_flow(loaded, {"settle_at": "2026-07-01"}, num_workers=0)
    blob = paused.checkpoint.dumps()
    assert paused.checkpoint.num_workers == 0

    # cross-process round-trip, resumed POOLED via the override (resume_flow passthrough)
    ckpt = RunCheckpoint.loads(blob)
    res = resume_flow(load_flow(_RESUME_FANOUT), checkpoint=ckpt, num_workers=4,
                      commands=[DeliverAnswerCommand(node_id="settle", value=None)])
    assert res.status == "succeeded", res.error

    # serial durable resume for the oracle
    ser = resume_flow(load_flow(_RESUME_FANOUT), checkpoint=RunCheckpoint.loads(blob),
                      commands=[DeliverAnswerCommand(node_id="settle", value=None)])
    assert res.output == ser.output
