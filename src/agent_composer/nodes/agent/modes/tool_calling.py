"""tool_calling mode — ask, run requested tools, repeat until answered.

Bind the node's tools, then loop: ask the model; if it requests ordinary tools,
run them via `TOOL_REGISTRY` and feed results back; if it requests a *control tool*
(e.g. `ask_user`), lower the pause to a continuation `Enqueue` (a human_input leaf +
a resume_agent node) and let the engine grow the live graph.

The agent loop body is the pure, self-contained `agent_step(messages, pending,
iterations, ctx) -> Output | Enqueue`: the re-entry frame rides as
arguments/return, never a private namespace. On a final answer it returns `Output`;
on a control call it returns the continuation pair as an `Enqueue`. The injected
answer is delivered to the human_input leaf as its `Output` and read by the
resume_agent via the bare `${<hi>.output}` forward-ref edge.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    messages_to_dict,
)

from agent_composer.nodes.agent.controls import CONTROL_TOOLS
from agent_composer.nodes.agent.modes.common import (
    DEFAULT_SYSTEM,
    AgentLoopError,
    AgentRunContext,
    register_mode,
)
from agent_composer.nodes.agent.modes.utils import text_of
from agent_composer.nodes.base import Enqueue, Output

MAX_TOOL_ITERATIONS = 8


def _slug_call_id(call_id: str) -> str:
    """Path-safe slug for a tool-call id embedded in a node id / `${...}` ref.
    `_PATH_RE` (expr/template.py) allows `_ # /` but not `-`; provider call ids may be
    uuids (Ollama) containing dashes, which break ref parsing on resume. Map every
    non-[A-Za-z0-9_] char to `_`. The REAL id is kept verbatim in `pending["call_id"]`
    and the human-input `slot` for the ToolMessage / resume-id match."""
    return re.sub(r"[^A-Za-z0-9_]", "_", call_id)


def run_tool(name: str, args: dict[str, Any]) -> str:
    """Execute one registered ordinary tool (same `TOOL_REGISTRY` path as a TOOL node).
    Errors come back as text for the model to see rather than crashing the node."""
    from agent_composer.tools import TOOL_REGISTRY

    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return f"Tool {name!r} is not available to this node."
    try:
        return str(tool.invoke(args))
    except Exception as exc:  # noqa: BLE001
        return f"Tool {name!r} failed: {exc}"


def agent_step(
    messages: list[BaseMessage],
    pending: Optional[dict],
    iterations: int,
    ctx: AgentRunContext,
):
    """Run ONE segment of the agent loop — the re-entry frame rides as args/return.

    On ENTRY this ALWAYS invokes the model on the passed-in `messages` — there is no
    resume-replay branch (the answer for a `pending` control call is appended as a
    `ToolMessage` by the agent's `Resume` entry — `AgentNode.run` — *before* this is called,
    so `pending` is `None` here). On a final answer -> `Output`. On a control call -> `Enqueue` of the continuation
    PAIR: a `human_input` descriptor + a `resume_agent` descriptor carrying the re-entry
    frame as DATA (memo / iterations / config-as-data / pending) and reading `answer` via
    the BARE forward-ref `${<hi>.output}` (the `outputs` head, NO `.output` suffix).
    Data-tool calls in the turn are flushed into `messages` before the `Enqueue`.
    """
    from agent_composer.tools import resolve_tools

    control_set = set(ctx.controls)
    bound = resolve_tools(list(ctx.tools)) + [CONTROL_TOOLS[n].tool for n in ctx.controls]
    chat = ctx.model.bind_tools(bound) if bound else ctx.model

    while iterations < MAX_TOOL_ITERATIONS:
        reply = chat.invoke(messages)
        iterations += 1
        messages.append(reply)
        calls = getattr(reply, "tool_calls", None) or []
        if not calls:
            # Final answer. A declared non-text `output:` forces a structured emit turn
            # (native or prompt-injection, with capped self-correction); a bare-str/Literal
            # shape keeps the text path. The structured value is enforced at the engine write
            # boundary on BOTH the primary path (`pool.set(..., declared=output_shape)`) and a
            # resumed agent's alias-filler path, so either re-entry validates it.
            from agent_composer.nodes.agent.structured import generate_structured, shape_to_schema

            if ctx.output_shape is not None and shape_to_schema(ctx.output_shape) is not None:
                return Output(
                    value=generate_structured(
                        ctx.model,
                        messages,
                        ctx.output_shape,
                        max_retries=ctx.retries,
                        llm_config=ctx.llm_config,
                    )
                )
            return Output(value=text_of(reply))

        # Run all data-tool calls first so every call in the turn gets answered.
        for call in (c for c in calls if c.get("name") not in control_set):
            messages.append(
                ToolMessage(
                    content=run_tool(call.get("name") or "", call.get("args") or {}),
                    tool_call_id=call.get("id") or "",
                )
            )

        control_calls = [c for c in calls if c.get("name") in control_set]
        if control_calls:
            if len(control_calls) > 1:
                raise AgentLoopError(
                    f"agent node {ctx.node_id!r}: multiple control-tool calls in one turn "
                    f"are not supported"
                )
            call = control_calls[0]
            call_id = call.get("id") or ""
            slug = _slug_call_id(call_id)
            pending = {
                "name": call["name"],
                "call_id": call_id,  # REAL id: matched to the ToolMessage tool_call_id on resume
                "args": call.get("args") or {},
            }
            hi_id = f"__ask#{slug}"  # slug keeps the node id / answer ref `_PATH_RE`-safe
            human_input = {
                "kind": "human_input",
                "node_id": hi_id,
                "prompt": str(call.get("args", {}).get("question", "")),
                "slot": call_id,  # REAL id: mints the resume-node id, never parsed by _PATH_RE
            }
            resume = {
                "kind": "resume_agent",
                "memo": messages_to_dict(messages),
                "iterations": iterations,
                "pending": pending,
                "answer": f"${{{hi_id}.output}}",  # node-first ref
                "llm_config": ctx.llm_config,
                "tools": list(ctx.tools),
                "controls": list(ctx.controls),
                "mode": "tool_calling",
            }
            return Enqueue(target=[human_input, resume], inputs={})

    raise AgentLoopError(
        f"agent node {ctx.node_id!r} hit the tool-iteration cap "
        f"({MAX_TOOL_ITERATIONS}) without a final answer"
    )


@register_mode("tool_calling")
def tool_calling(ctx: AgentRunContext):
    messages: list[BaseMessage] = [
        SystemMessage(content=DEFAULT_SYSTEM),
        HumanMessage(content=ctx.prompt),
    ]
    return agent_step(messages, None, 0, ctx)
