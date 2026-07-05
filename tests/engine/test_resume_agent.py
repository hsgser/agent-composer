"""The agent **Resume entry** — the continuation of an `ask_user` pause (G2).

A resumed agent is an `AgentNode` with a `Resume` entry (same `kind = AGENT`, no separate
kind). Its `run(inputs) -> Output | Grow`: rebuild `messages` via
`messages_from_dict(memo)`, append `inputs["answer"]` as the `ToolMessage` matching
`pending["call_id"]`, then call `agent_step` (which invokes the model exactly once on
entry — the memo is replayed as messages, not re-invoked). No scratch.
"""

from langchain_core.messages import AIMessage, SystemMessage, HumanMessage, messages_to_dict

from agent_composer.nodes.agent.node import AgentNode, Resume
from agent_composer.nodes.base import NodeKind, Output


class _Chat:
    def __init__(self, replies):
        self._r = list(replies)
        self.calls = 0

    def bind_tools(self, t):
        return self

    def invoke(self, m):
        self.calls += 1
        return self._r.pop(0)


def _memo():
    return messages_to_dict([
        SystemMessage(content="s"),
        HumanMessage(content="go"),
        AIMessage(content="", tool_calls=[{"name": "ask_user",
            "args": {"question": "ok?"}, "id": "q1", "type": "tool_call"}]),
    ])


def test_resume_entry_appends_answer_and_finishes(monkeypatch):
    import agent_composer.llm_clients as llm
    chat = _Chat([AIMessage(content="FINAL")])
    monkeypatch.setattr(llm, "model_from_config", lambda cfg: chat)
    node = AgentNode("agent",
                     entry=Resume(memo=_memo(), iterations=1,
                                  pending={"name": "ask_user", "call_id": "q1", "args": {}}),
                     llm_config=None, tools=[], controls=["ask_user"], mode="tool_calling")
    # The continuation is an ordinary AGENT (one closed kind); the Resume entry marks the arm.
    assert node.kind == NodeKind.AGENT
    assert isinstance(node.entry, Resume)
    out = node.run({"answer": "yes"})
    assert isinstance(out, Output) and out.value == "FINAL"
    assert chat.calls == 1   # only the post-resume turn; the first turn replayed from memo, not re-invoked
