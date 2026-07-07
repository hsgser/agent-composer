"""The AGENT node (mode + skills) with a fake langchain model.

No API keys: `model_from_config` is monkeypatched to return a scripted fake chat
model that yields canned `AIMessage`s; tool execution goes through `TOOL_REGISTRY`
(monkeypatched). Covers `plain` mode, `tool_calling` (plain answer, a tool
round-trip, the iteration cap), and the `ask_user` control-tool suspend/resume.

(The AGENT-through-the-loader run path is covered once the Compose-loader agent
run lands; AGENT-mode compile-time validation moved with the v0 compiler.)
"""

import pytest
from langchain_core.messages import AIMessage

import agent_composer.llm_clients as llm_clients_mod
import agent_composer.tools as tools_mod
from agent_composer.compile.model import END_ID, START_ID, CompiledFlow, Edge, FlowOutput
from agent_composer.nodes.end import EndNode
from agent_composer.nodes.start import StartNode
from agent_composer.events import NodeFailed, RunSucceeded
from agent_composer.nodes.agent import AgentNode
from agent_composer.runtime.engine import FlowEngine
from agent_composer.typesys.pool import VariablePool
from agent_composer.llm_clients import LLMConfig


class _FakeChat:
    """Returns queued AIMessages in order; records calls and bound tools."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.calls += 1
        return self._replies.pop(0)


def _ai_tool_call(name, args, call_id="1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _patch_model(monkeypatch, chat):
    monkeypatch.setattr(llm_clients_mod, "model_from_config", lambda cfg: chat)


class _FakeTool:
    def __init__(self, fn):
        self._fn = fn
        self.seen = []

    def invoke(self, args):
        self.seen.append(args)
        return self._fn(args)


def _node(tools=None, prompt="hi", mode="tool_calling"):
    return AgentNode("n", prompt=prompt, tools=tools or [], llm_config=LLMConfig(), mode=mode)


def _run_node(node, pool=None):
    """Drive the node's contract like the engine does (via the `eval_node` seam) and
    return the TERMINAL event — `NodeSucceeded` on a final answer, `NodeFailed` if the
    node raised (e.g. the iteration cap, converted at the boundary)."""
    from agent_composer.runtime.eval_node import eval_node

    return list(eval_node(node, None, pool or VariablePool()))[-1]


def test_plain_mode_single_call_ignores_tools(monkeypatch):
    chat = _FakeChat([AIMessage(content="plain answer")])
    _patch_model(monkeypatch, chat)
    # tools listed but plain mode must not bind or loop — one call, no tool use
    assert _run_node(_node(tools=["value"], mode="plain")).output == "plain answer"
    assert chat.calls == 1
    assert chat.bound_tools is None


def test_tool_calling_returns_plain_text(monkeypatch):
    _patch_model(monkeypatch, _FakeChat([AIMessage(content="answer")]))
    assert _run_node(_node()).output == "answer"


def test_tool_calling_round_trip(monkeypatch):
    tool = _FakeTool(lambda a: f"px:{a['topic']}")
    monkeypatch.setitem(tools_mod.TOOL_REGISTRY, "value", tool)
    chat = _FakeChat([
        _ai_tool_call("value", {"topic": "ACME"}),
        AIMessage(content="done"),
    ])
    _patch_model(monkeypatch, chat)
    assert _run_node(_node(tools=["value"])).output == "done"
    assert tool.seen == [{"topic": "ACME"}]   # the tool actually ran
    assert chat.calls == 2                      # ask -> tool -> ask


def test_tool_calling_iteration_cap_fails(monkeypatch):
    from agent_composer.nodes.agent.modes.tool_calling import MAX_TOOL_ITERATIONS

    monkeypatch.setitem(tools_mod.TOOL_REGISTRY, "noop", _FakeTool(lambda a: "ok"))
    # always asks for a tool -> never answers -> trips the cap. The cap `raise
    # AgentLoopError` is converted to NodeFailed by the engine boundary (eval_node).
    # One reply per allowed turn is enough to reach (and trip) the cap.
    looping = _FakeChat([_ai_tool_call("noop", {}, str(i)) for i in range(MAX_TOOL_ITERATIONS)])
    _patch_model(monkeypatch, looping)
    ev = _run_node(_node(tools=["noop"]))
    assert isinstance(ev, NodeFailed)
    assert ev.error_type == "AgentLoopError"
    assert "tool-iteration cap" in ev.error


def test_agent_run_has_no_scratch_kwarg():
    # AgentNode.run is a pure `run(self, inputs)` — the agent memo rides as graph
    # data through the resume_agent continuation, never a `scratch` cap.
    import inspect

    assert "scratch" not in inspect.signature(AgentNode.run).parameters


def test_ask_user_in_memory_continuation_round_trip(monkeypatch):
    # The OLD model (checkpoint restore + __agent_state__:agent / answer:q1) is dead.
    # ask_user now pauses on a NAMESPACED human_input leaf id; resume delivers the
    # answer as that leaf's Output (deliver-as-Output), and the resume_agent
    # continuation finishes — without re-invoking turn 1 (memo replayed). Durable
    # paused-checkpoint resume of an agent is deferred.
    from types import SimpleNamespace

    from agent_composer.events import RunPaused, RunResumed
    from agent_composer.compose.run import resume_command
    from agent_composer.suspension.pause import HumanInputRequired

    chat = _FakeChat([
        _ai_tool_call("ask_user", {"question": "Approve the action?"}, call_id="q1"),
        AIMessage(content="FINAL"),
    ])
    _patch_model(monkeypatch, chat)
    node = AgentNode("agent", prompt="go", controls=["ask_user"],
                     llm_config=LLMConfig(), mode="tool_calling")
    graph = CompiledFlow.from_parts(
        {"agent": node, START_ID: StartNode(START_ID, input_decls=[]),
         END_ID: EndNode.record(END_ID, output_names=["output"])},
        # END_ID is record-mode; its `output` param binds ${agent.output} via the producer edge.
        [Edge("e0", START_ID, "agent"),
         Edge("agent->__end__#0", "agent", END_ID, input_group="output")],
        outputs=[FlowOutput(name="output", from_="${agent.output}")],
        wiring={END_ID: {"output": "${agent.output}"}},
    )
    eng = FlowEngine(graph)
    evs = list(eng.run())
    assert isinstance(evs[-1], RunPaused)
    reason = evs[-1].reasons[0]
    assert isinstance(reason, HumanInputRequired) and reason.prompt == "Approve the action?"
    # the parked id is the NAMESPACED leaf (callsite=spawner "agent"), not "agent" itself
    assert reason.node_id.startswith("agent/") and reason.node_id in eng.flow.nodes
    assert chat.calls == 1
    cmd = resume_command(SimpleNamespace(compiled=graph), reason, "yes, approved")
    evs2 = list(eng.resume(commands=[cmd]))
    assert isinstance(evs2[0], RunResumed)
    assert isinstance(evs2[-1], RunSucceeded) and evs2[-1].output == "FINAL"
    assert chat.calls == 2  # turn 1 replayed from carried memo, NOT re-invoked
