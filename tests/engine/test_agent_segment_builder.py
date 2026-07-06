"""The pure AGENT-pause builder `agent_segment_subgraph` — pins the continuation shape.

`agent_segment_subgraph(pair, callsite)` wraps `clone_continuation_pair`, bakes a PROVISIONAL
`commit_as=callsite` on the resume terminal (the engine residual overrides it to the true
multi-pause origin), and returns a `Flow` (the fragment an AGENT grows into on a control
pause). This test pins: the human_input leaf is the `start_id`; the resume terminal (`end_id`)
carries the provisional `commit_as == callsite`; both node ids are `ns(callsite, …)`-prefixed.
"""

from agent_composer.compile.expand import agent_segment_subgraph, ns
from agent_composer.compile.model import Flow


def _pair():
    """A synthetic agent-pause continuation PAIR, as `agent_step` emits it."""
    hi_desc = {"kind": "human_input", "node_id": "__ask#q1", "prompt": "?", "slot": "q1"}
    resume_desc = {
        "kind": "resume_agent",
        "memo": [],
        "iterations": 0,
        "pending": {"name": "ask_user", "call_id": "q1", "args": {}},
        "answer": "${__ask#q1.output}",
        "llm_config": None,
        "tools": [],
        "controls": [],
        "mode": "tool_calling",
    }
    return [hi_desc, resume_desc]


def test_agent_segment_subgraph_matches_continuation_type():
    sg = agent_segment_subgraph(_pair(), callsite="a0")

    assert isinstance(sg, Flow)

    hi_id = ns("a0", "__ask#q1")
    resume_id = ns("a0", "__resume#q1")

    # start_id == the human_input leaf (0 incoming edges -> the leaf-pause path).
    assert sg.start_id == hi_id
    # Terminal (end_id) == the resume node; provisional commit_as == callsite (engine overrides it).
    assert sg.end_id == resume_id
    assert sg.nodes[resume_id].commit_as == "a0"
    # Both node ids are namespaced under the callsite.
    assert set(sg.nodes) == {hi_id, resume_id}
    assert all(nid.startswith("a0/") for nid in sg.nodes)
