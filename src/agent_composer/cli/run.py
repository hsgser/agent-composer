"""`ac run` — load a flow, gather inputs, drive it to a terminal, render the output.

Inputs come from flags (`--input k=v`, repeatable; `--inputs file.json`); any declared
input still missing is prompted for interactively (required ones are starred). A run that
suspends on a HUMAN_INPUT / WAIT effect is resumed interactively — each pause prompts for
the awaited value and the run continues to a terminal. The answer's type is enforced at
the engine boundary; an invalid one fails the run.

`--provider`/`--model` feed the outermost layer of the `llm_config` cascade (fill-the-gap,
not a hard override): they fill only the fields an agent and its enclosing flow leave unset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from agent_composer.compile.model import END_ID, START_ID
from agent_composer.compose.errors import LoadError
from agent_composer.compose.loader import load_flow
from agent_composer.compose.parser import node_lines
from agent_composer.compose.run import RunResult, resume_command, resume_flow, run_flow
from agent_composer.events import NodeFailed
from agent_composer.state.segments import SegmentType

console = Console()
err_console = Console(stderr=True)

# Lines of `.yaml` context shown above and below the offending line in the error panel
# (a window, so a large flow doesn't dump in full — mirrors a Python traceback's code frame).
_ERR_CONTEXT = 5


def _render_source_frame(text: str, marks, title: str, message: str) -> None:
    """Print an error as a boxed `.yaml` source frame — shared by compile and runtime errors.

    `marks` are the 1-based offending lines: the panel shows the source around them with line
    numbers and each mark highlighted (like a Python traceback's code box), titled
    `title:line[,line...]`, with `message` in red below. The window stretches from the first to
    the last mark (+ padding), so a multi-point error shows every implicated line. When no mark
    is in range, prints a plain `title: message` line (no box)."""
    lines = text.splitlines()
    marks = sorted({m for m in marks if 1 <= m <= len(lines)})
    if not marks:
        err_console.print(Text(f"{title}: ", style="red bold") + Text(message, style="red"))
        return
    start = max(1, marks[0] - _ERR_CONTEXT)
    end = min(len(lines), marks[-1] + _ERR_CONTEXT)
    frame = Syntax(
        text,
        "yaml",
        line_numbers=True,
        line_range=(start, end),
        highlight_lines=set(marks),
        word_wrap=True,
    )
    panel_title = f"{title}:{','.join(str(m) for m in marks)}"
    err_console.print(Panel(frame, title=panel_title, title_align="left", border_style="red"))
    err_console.print(Text(message, style="red"))


def _render_load_error(err: LoadError, flow: Path, text: str) -> None:
    """Print a located compile error for the author as a boxed `.yaml` source frame.

    Surfaces `LoadError.line`/`.lines` (the loader's source-line tracking) at the CLI boundary
    so a failed compile points at WHERE in the `.yaml` it broke. A multi-line error (e.g. a cycle,
    which implicates several nodes) highlights ALL of them. When no line is known (or all are out
    of range), prints `file: <message>` with no frame. The "why" legend (`.notes`) follows.
    """
    marks = err.lines or ([err.line] if err.line is not None else [])
    _render_source_frame(text, marks, flow.name, str(err))
    _render_notes(err)


def _render_run_error(result: RunResult, flow: Path, text: str) -> None:
    """Print a runtime failure as a located `.yaml` frame at the node that raised.

    A node failure carries the offending node id (the last `NodeFailed` event); map it to its
    source line (`node_lines`) and box the frame there, mirroring the compile-error UX. A failure
    with no node behind it — a false boundary/post assert, or an input-coercion error at the run
    boundary — has no node line to point at, so it falls back to a plain `run <status>: <message>`
    line."""
    failed = [e for e in result.events if isinstance(e, NodeFailed)]
    node_id = failed[-1].node_id if failed else None
    line = node_lines(text).get(node_id) if node_id else None
    message = result.error or "(no detail)"
    if line is None:
        err_console.print(f"[red]run {result.status}: {message}[/red]")
        return
    _render_source_frame(text, [line], flow.name, message)


def _render_notes(err: LoadError) -> None:
    """Print the error's "why" legend (`LoadError.notes`) under the message, if any.

    Each note is an explanatory line the source frame can't show — e.g. the dependency
    edges that close a cycle — rendered indented and dim so it reads as context, not a
    second error.
    """
    for note in err.notes or []:
        err_console.print(Text(f"  ↳ {note}", style="yellow"))


class _ProgressReporter:
    """Render per-node progress for `ac run` as the engine streams node events.

    A node shows a live spinner while running, then is rewritten in place as a green
    `✓ <node>` on success or a red `✗ <node>` (with the error on the next line) on
    failure. With `verbose`, each node's output is printed under its check.

    `on_event` is invoked on a single thread, but several nodes can be *running* at the
    same time (a fan-out), so `_running` is a map and the live region shows one spinner
    per member. On a real terminal the spinners animate in a Rich `Live` region and the
    finished lines scroll above it; off a terminal (a pipe, CI, the test runner) there
    is no spinner — only the final `✓`/`✗` line per node is printed.

    Progress goes to stderr so the flow's actual output on stdout stays pipeable.
    """

    def __init__(self, console: Console, verbose: bool) -> None:
        self._console = console
        self._verbose = verbose
        # node_id -> its live Spinner, for every node currently running.
        self._running: Dict[str, Spinner] = {}
        self._live: Any = None  # rich.live.Live while active on a terminal, else None

    @property
    def is_live(self) -> bool:
        return self._live is not None

    def start(self) -> None:
        """Begin a live spinner region (terminal only). Idempotent."""
        if self._live is not None or not self._console.is_terminal:
            return
        from rich.live import Live

        self._live = Live(console=self._console, refresh_per_second=12, transient=True)
        self._live.start()
        self._refresh()

    def stop(self) -> None:
        """Tear down the live region (e.g. before a questionary prompt). Idempotent."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _refresh(self) -> None:
        """Redraw the live region with one spinner line per running node."""
        if self._live is not None:
            self._live.update(Group(*self._running.values()))

    def _emit(self, renderable: Any) -> None:
        """Print a permanent line; under a live region it scrolls above the spinners."""
        self._console.print(renderable)

    def handle(self, event: Any) -> None:
        """Fold one engine event into the display. Boundary nodes are ignored."""
        node_id = getattr(event, "node_id", None)
        if node_id in (START_ID, END_ID):
            return
        name = type(event).__name__
        if name == "NodeStarted":
            self._running[node_id] = Spinner("dots", text=Text(node_id, style="cyan"))
            self._refresh()
        elif name == "NodeSucceeded":
            self._running.pop(node_id, None)
            self._emit(Text(f"✓ {node_id}", style="green"))
            if self._verbose:
                self._emit_output(event.output)
            self._refresh()
        elif name == "NodeFailed":
            self._running.pop(node_id, None)
            self._emit(Text(f"✗ {node_id}", style="red bold"))
            self._emit(Text(f"    {event.error}", style="red"))
            self._refresh()

    def _emit_output(self, output: Any) -> None:
        """Print a node's produced value, indented under its check (verbose only)."""
        body = output if isinstance(output, str) else repr(output)
        for line in body.splitlines() or [""]:
            self._emit(Text(f"    {line}", style="dim"))


def _parse_kv(pairs: List[str]) -> Dict[str, Any]:
    """Parse repeated `--input k=v` flags into a dict (values stay strings; the engine
    coerces them against each input's declared type at the run boundary)."""
    out: Dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"--input must be k=v (got {pair!r})")
        key, value = pair.split("=", 1)
        out[key.strip()] = value
    return out


# Example value for the scalars whose accepted string form isn't self-evident from the
# type name — the ISO-8601 date/datetime, which the engine parses with
# `date.fromisoformat` / `datetime.fromisoformat` (a bare date is not a valid datetime).
_FORMAT_EXAMPLE: Dict[Any, str] = {
    SegmentType.DATE: "2026-05-21",
    SegmentType.DATETIME: "2026-05-21T14:30",
}


def _format_hint(shape: Any) -> Optional[str]:
    """An `e.g. <value>` example for an input whose string format isn't obvious, or
    `None` when none is needed. Keyed off the shape's scalar `seg_type`, so it fires for
    `Optional[date]` too (the resolved shape stays `DATE`, just nullable)."""
    example = _FORMAT_EXAMPLE.get(getattr(shape, "seg_type", None))
    return f"e.g. {example}" if example else None


def _input_label(decl: Any) -> str:
    """The questionary prompt label for one declared input.

    Carries the input's name, declared `type`, a required (`*`) / `optional` mark,
    any default, and — for an ISO-8601 scalar with no default — an example value (a
    default already shows the format, so the example is dropped there). So an author at
    the prompt sees what is expected without reading the `.yaml`. Shapes:
        `topic (str) *`                                      (required)
        `as_of (Optional[date]) [optional] e.g. 2026-05-21`  (date: example shown)
        `as_of (Optional[date]) [default: 2026-05-21]`       (default shows the format)
        `window (int) [default: 30]`                         (optional, has a default)
    """
    parts = [decl.name]
    if getattr(decl, "type", None):
        parts.append(f"({decl.type})")
    hint = _format_hint(decl.shape)
    if decl.required:
        parts.append("*")
        if hint:
            parts.append(hint)
    elif decl.default is not None:
        # The default value itself shows the accepted format, so skip the example.
        parts.append(f"[default: {decl.default}]")
    else:
        parts.append("[optional]")
        if hint:
            parts.append(hint)
    return " ".join(parts)


def _flow_banner(
    name: Optional[str], description: Optional[str], version: Optional[str] = None
) -> Optional[Panel]:
    """The "what am I running" banner printed at the start of a run (to stderr).

    A boxed panel of the flow's identity — `name`/`description`/`version` — so the
    author sees what the run is before inputs/progress. Returns `None` when there is no
    metadata at all (nothing to show)."""
    if not (name or description or version):
        return None
    body = Text()
    body.append("Running flow: ", style="bold")
    body.append(name or "(unnamed)")
    if version:
        body.append(f"  (version: {version})", style="dim")
    if description:
        body.append("\nDescription: ", style="bold")
        body.append(description)
    return Panel(body, border_style="cyan", expand=False)


def _prompt_missing(decls: List[Any], have: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Prompt for each declared input not already supplied. Returns the gathered values,
    or None if the user cancels (Ctrl-C / Esc). The widget follows the declared type: a
    boolean is a confirm, a `Literal[...]` enum is a select, everything else is free text.

    Each prompt's label carries the input's type + a required/optional mark + default
    (see `_input_label`)."""
    import questionary

    gathered: Dict[str, Any] = {}
    for decl in decls:
        if decl.name in have:
            continue
        label = _input_label(decl)
        shape = decl.shape
        if shape.seg_type == SegmentType.BOOLEAN:
            value = questionary.confirm(label, default=bool(decl.default)).ask()
        elif shape.tags:  # a Literal[...] enum
            value = questionary.select(label, choices=sorted(shape.tags)).ask()
        else:
            default = "" if decl.default is None else str(decl.default)
            value = questionary.text(label, default=default).ask()

        if value is None:  # Ctrl-C / Esc
            return None
        if isinstance(value, str) and value.strip() == "" and not decl.required:
            continue
        gathered[decl.name] = value
    return gathered


def _resume_to_terminal(
    loaded: Any, result: RunResult, reporter: _ProgressReporter, on_event: Any
) -> RunResult:
    """Drive a paused run to a terminal, prompting for each pause's awaited value.

    A HUMAN_INPUT pause prompts for the answer; a timed WAIT asks to release it now; an
    external-event pause can't be satisfied here and stays paused. Cancelling a prompt
    leaves the run paused (not an error).

    The live spinner region is torn down before each questionary prompt (so the prompt
    isn't fought over by the animation) and brought back up to stream the resumed run.
    `on_event` is None under `--quiet`, in which case no spinner is shown."""
    import questionary

    while result.status == "paused":
        reporter.stop()
        answered: List[Tuple[Any, Any]] = []
        for reason in result.pause_reasons:
            if reason.type == "human_input_required":
                label = reason.prompt or f"input for {reason.node_id}"
                answer = questionary.text(label).ask()
                if answer is None:
                    return result  # cancelled — stay paused
                answered.append((reason, answer))
            elif reason.type == "scheduled_pause":
                if not questionary.confirm(
                    f"{reason.node_id}: release the wait now?", default=True
                ).ask():
                    return result
                answered.append((reason, None))  # release: value=None
            else:
                err_console.print(
                    "[yellow]awaiting an external event — can't release it here[/yellow]"
                )
        if not answered:
            return result
        commands = [resume_command(loaded, reason, value) for reason, value in answered]
        if on_event is not None:
            reporter.start()
        result = resume_flow(loaded, engine=result.engine, commands=commands, on_event=on_event)
    return result


def run(
    flow: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Path to a flow .yaml"),
    input: List[str] = typer.Option(  # noqa: A002 - matches the user-facing flag name
        None, "--input", "-i", help="An input as k=v (repeatable)."
    ),
    inputs: Optional[Path] = typer.Option(
        None, "--inputs", exists=True, dir_okay=False, readable=True, help="A JSON file of inputs."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-node progress."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Also print each node's output as it finishes."
    ),
    num_workers: int = typer.Option(
        0,
        "--num-workers",
        "-w",
        min=0,
        help="Worker pool size. 0 = single-threaded (deterministic); "
        ">=1 runs independent ready nodes (a fan-out) concurrently.",
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="Override the LLM provider for agents that set none (cascade)."
    ),
    model: Optional[str] = typer.Option(
        None, "--model", help="Override the LLM model for agents that set none (cascade)."
    ),
    engine_trace: bool = typer.Option(
        False,
        "--engine-trace",
        help="On a compile error, also print the engine Python traceback (for debugging "
        "the engine itself); by default only the located `.yaml` error is shown.",
    ),
) -> None:
    """Run a flow to completion and print its output."""
    text = flow.read_text()
    try:
        loaded = load_flow(text, search_paths=[flow.parent])
    except LoadError as err:
        # An author's flow failed to compile: point at WHERE in the `.yaml` it broke, not at
        # the engine internals. `--engine-trace` adds the Python traceback for engine debugging.
        _render_load_error(err, flow, text)
        if engine_trace:
            err_console.print_exception()
        raise typer.Exit(code=1)

    # The "what am I running" banner (flow name/description/version) — stderr, like
    # progress; suppressed under --quiet.
    if not quiet:
        banner = _flow_banner(loaded.name, loaded.description, loaded.version)
        if banner is not None:
            err_console.print(banner)

    supplied: Dict[str, Any] = {}
    if inputs is not None:
        supplied.update(json.loads(inputs.read_text()))
    if input:
        supplied.update(_parse_kv(input))

    prompted = _prompt_missing(loaded.input, supplied)
    if prompted is None:
        err_console.print("[yellow]run cancelled[/yellow]")
        raise typer.Exit(code=1)
    supplied.update(prompted)

    # `--quiet` silences progress entirely; otherwise stream node events to the reporter.
    # `--verbose` adds each node's output. `verbose` implies progress even with no spinner.
    reporter = _ProgressReporter(err_console, verbose=verbose)
    on_event = None if quiet else reporter.handle

    # The CLI flags supply the OUTERMOST cascade layer (fill-the-gap), not a hard override:
    # an agent's own llm_config and a flow-level llm_config: still win per field.
    cli_cfg = {k: v for k, v in {"provider": provider, "model": model}.items() if v}
    if not quiet:
        reporter.start()
    try:
        result = run_flow(
            loaded, supplied, on_event=on_event, llm_config=cli_cfg or None,
            num_workers=num_workers,
        )
        if result.status == "paused":
            result = _resume_to_terminal(loaded, result, reporter, on_event)
    finally:
        reporter.stop()

    if result.status == "succeeded":
        out = result.output
        if isinstance(out, str) and out.strip():
            console.print(Markdown(out))
        else:
            console.print(out)
    elif result.status == "paused":
        err_console.print("[yellow]run paused (resume cancelled)[/yellow]")
        raise typer.Exit(code=1)
    else:
        # A node failure points at WHERE in the `.yaml` it raised (boxed frame), mirroring a
        # compile error; a node-less failure (assert/input-coercion) prints the plain message.
        _render_run_error(result, flow, text)
        raise typer.Exit(code=1)
