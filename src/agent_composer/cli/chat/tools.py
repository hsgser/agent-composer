"""Flow-op tools for the `ac chat` composer assistant.

A CLI-HOST concern, not engine core (the core ships no domain tools). Each tool is a
`@register_tool`-decorated function; importing this module self-registers them. All
paths resolve against a workspace root set by `set_workspace` at `ac chat` startup and
escapes (absolute paths, `..`) are rejected.
"""
from __future__ import annotations
from pathlib import Path
from agent_composer.tools import register_tool

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
