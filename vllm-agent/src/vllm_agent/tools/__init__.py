"""Worker tool registry. Tools register themselves here at import time."""
from __future__ import annotations

from .base import Tool, ToolContext

WORKER_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in WORKER_TOOLS:
        raise ValueError(f"tool {tool.name!r} already registered")
    WORKER_TOOLS[tool.name] = tool
    return tool


__all__ = ["Tool", "ToolContext", "WORKER_TOOLS", "register"]
