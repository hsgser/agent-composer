"""`ac chat` — an interactive REPL dogfooded as a flow.

Loads the bundled composer chat flow (or a user-supplied one), runs it, and drives the
per-turn human_input suspend/resume: print the assistant's latest reply, prompt the
user, resume with the message. `/exit` or EOF ends the session. The turn loop and
tools live here; the engine sees only an ordinary flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown

from agent_composer.cli.chat import tools as chat_tools
from agent_composer.cli.run import (  # reused helpers
    _ensure_cwd_importable,
    _ensure_provider_keys,
    _last_segment,
)
from agent_composer.compose.loader import load_flow
from agent_composer.compose.run import resume_command, resume_flow, run_flow
from agent_composer.events import NodeSucceeded

console = Console()

# The composer chat flow shipped with the package — the default when no flow is given.
_BUNDLED = Path(__file__).parent / "chat.yaml"


def chat(
    flow: Optional[Path] = typer.Argument(
        None, exists=True, dir_okay=False, readable=True,
        help="A chat flow .yaml (default: the bundled composer chat).",
    ),
    workspace: Path = typer.Option(
        Path.cwd(), "--workspace", "-C",
        help="Directory the flow-op tools are confined to.",
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="Override the LLM provider for agents that set none (cascade)."
    ),
    model: Optional[str] = typer.Option(
        None, "--model", help="Override the LLM model for agents that set none (cascade)."
    ),
) -> None:
    """Start an interactive chat session over a chat flow."""
    _ensure_cwd_importable()  # so a chat flow's `code: pkg.mod:fn` fold ref resolves (mirrors `ac run`)
    chat_tools.set_workspace(workspace)
    path = flow or _BUNDLED
    loaded = load_flow(path.read_text(), search_paths=[path.parent, workspace])

    # The CLI flags supply the OUTERMOST cascade layer (fill-the-gap), not a hard override.
    cli_cfg = {k: v for k, v in {"provider": provider, "model": model}.items() if v}
    _ensure_provider_keys(loaded, cli_cfg)

    # The reply node's latest output, captured off the event stream each turn so it can be
    # printed before the next prompt. The reply node lives inside the per-turn LOOP body, so
    # its runtime id is namespaced (`chat/reply`, `chat~1/reply`) — `_last_segment` maps it
    # back to the authored `reply`. `None` between turns (cleared once printed).
    latest: dict[str, Any] = {"reply": None}

    def on_event(ev: Any) -> None:
        if isinstance(ev, NodeSucceeded) and _last_segment(ev.node_id) == "reply":
            latest["reply"] = ev.output

    # The chat flow immediately pauses on the first `human_input` (the turn's `ask`).
    result = run_flow(loaded, {"opening": ""}, on_event=on_event, llm_config=cli_cfg or None)
    while result.status == "paused":
        if latest["reply"] is not None:
            console.print(Markdown(str(latest["reply"])))
            latest["reply"] = None
        try:
            msg = console.input("[bold cyan]You:[/bold cyan] ")
        except (EOFError, KeyboardInterrupt):
            break
        if msg.strip() in {"/exit", "/quit"}:
            break
        # The pause is the turn's single `human_input`; deliver the typed line as its answer.
        reason = result.pause_reasons[0]
        cmd = resume_command(loaded, reason, msg)
        # Resume the SAME in-process run via the live engine. `llm_config` is not re-supplied
        # here: it is a no-op on the `engine=` path (the live graph already carries the baked
        # cascade from the initial run_flow; it only re-applies on the `checkpoint=` restore).
        result = resume_flow(loaded, engine=result.engine, commands=[cmd], on_event=on_event)
    console.print("[dim]session ended[/dim]")
