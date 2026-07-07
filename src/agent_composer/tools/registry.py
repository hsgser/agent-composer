"""Tool registry + the ``@register_tool`` decorator.

A tool self-registers by decorating its implementation; importing the module
that defines it populates ``TOOL_REGISTRY`` as a side effect. The engine's TOOL
node and the AGENT tool-calling loop look tools up here by id.

This is the package's tool *seam*: the core ships no domain tools — a host
application registers its own (e.g. ``@register_tool("add")``). Tool ids must be
unique across everything registered into the process.
"""

from __future__ import annotations

import inspect
from typing import Callable

from langchain_core.tools import BaseTool, StructuredTool

# Maps tool id -> the built LangChain tool. Populated by ``register_tool``.
TOOL_REGISTRY: dict[str, BaseTool] = {}


def register_tool(tool_id: str) -> Callable:
    """Register the decorated function as a tool under `tool_id`.

    The function's docstring becomes the tool description the model sees (dedented with
    `inspect.cleandoc`, so a multi-line docstring reads cleanly, not with source indentation).
    The original function is returned unchanged; the built LangChain tool is stored in
    `TOOL_REGISTRY`.

    Args:
        tool_id (`str`):
            The unique id to register under; a duplicate id raises.

    Returns:
        `Callable`: A decorator that registers its target and returns it unchanged.

    Raises:
        ValueError: If `tool_id` is already registered.

    Example:
        ```python
        @register_tool("add")
        def add(a: int, b: int) -> int:
            "Add two integers."
            return a + b
        ```
    """

    def decorator(fn: Callable) -> Callable:
        if tool_id in TOOL_REGISTRY:
            raise ValueError(f"Duplicate tool id '{tool_id}'")
        description = inspect.cleandoc(fn.__doc__ or "").strip() or tool_id
        TOOL_REGISTRY[tool_id] = StructuredTool.from_function(
            fn, name=tool_id, description=description
        )
        return fn

    return decorator


def resolve_tools(tool_ids: list[str]) -> list[BaseTool]:
    """Look up tools by id.

    Args:
        tool_ids (`list[str]`):
            The ids to resolve, in order.

    Returns:
        `list[BaseTool]`: The matching tools, in the same order as `tool_ids`.

    Raises:
        ValueError: If any id is not registered.
    """
    resolved = []
    for tid in tool_ids:
        if tid not in TOOL_REGISTRY:
            raise ValueError(
                f"Unknown tool id '{tid}'. Known tools: {sorted(TOOL_REGISTRY)}"
            )
        resolved.append(TOOL_REGISTRY[tid])
    return resolved
