"""Flow-op tools for the `ac chat` composer assistant.

A CLI-HOST concern, not engine core (the core ships no domain tools). Each tool is a
`@register_tool`-decorated function; importing this module self-registers them. All
paths resolve against a workspace root set by `set_workspace` at `ac chat` startup and
escapes (absolute paths, `..`) are rejected.
"""
from __future__ import annotations
import json
from pathlib import Path
from agent_composer.tools import register_tool
from agent_composer.compose.loader import load_flow
from agent_composer.compose.errors import LoadError
from agent_composer.compose.run import run_flow as _run_flow

# The workspace root every tool path is confined to. Set at `ac chat` startup.
_WORKSPACE: Path = Path.cwd()


def set_workspace(root: Path) -> None:
    """Set the directory all flow-op tools are confined to."""
    global _WORKSPACE
    _WORKSPACE = Path(root).resolve()


def _resolve(rel: str) -> Path:
    """Resolve `rel` under the workspace, rejecting absolute paths and `..` escapes."""
    p = (_WORKSPACE / rel).resolve()
    if p != _WORKSPACE and _WORKSPACE not in p.parents:
        raise ValueError(f"path escapes the workspace: {rel!r}")
    return p


@register_tool("list_flows")
def list_flows(subdir: str = "") -> str:
    "List the *.yaml flow files under the workspace (optionally within a subdirectory)."
    base = _resolve(subdir) if subdir else _WORKSPACE
    names = sorted(str(p.relative_to(_WORKSPACE)) for p in base.rglob("*.yaml"))
    return "\n".join(names) if names else "(no flows found)"


@register_tool("read_flow")
def read_flow(path: str) -> str:
    "Return the YAML text of a flow file under the workspace."
    return _resolve(path).read_text()


@register_tool("validate_flow")
def validate_flow(path: str) -> str:
    "Compile-check a flow without running it. Returns 'OK: <name>' or the compile error."
    text = _resolve(path).read_text()
    try:
        loaded = load_flow(text, search_paths=[_WORKSPACE])
    except LoadError as err:
        return f"INVALID: {err}"
    names = ", ".join(d.name for d in loaded.input) or "(none)"
    return f"OK: {loaded.name}; inputs: {names}"


@register_tool("run_flow")
def run_flow(path: str, inputs_json: str = "{}") -> str:
    "Run a flow NON-INTERACTIVELY with JSON inputs. Returns its output, or a paused/error note."
    text = _resolve(path).read_text()
    try:
        loaded = load_flow(text, search_paths=[_WORKSPACE])
        inputs = json.loads(inputs_json or "{}")
    except (LoadError, json.JSONDecodeError) as err:
        return f"ERROR: {err}"
    result = _run_flow(loaded, inputs)          # on_event=None -> non-interactive
    if result.status == "succeeded":
        return f"OUTPUT: {result.output}"
    if result.status == "paused":
        return "PAUSED: this flow needs interactive input; cannot run inside chat."
    return f"FAILED: {result.error}"


@register_tool("write_flow")
def write_flow(path: str, content: str) -> str:
    "Write a flow YAML file under the workspace. Validates it compiles BEFORE writing."
    dest = _resolve(path)                        # raises on escape
    try:
        load_flow(content, search_paths=[_WORKSPACE])
    except LoadError as err:
        return f"INVALID (not written): {err}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    return f"WROTE: {dest.relative_to(_WORKSPACE)}"
