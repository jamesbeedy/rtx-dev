"""Filesystem tools: read, write, edit, grep, glob."""
from __future__ import annotations

from typing import Any

from . import Tool, ToolContext, register


# ---- read_file --------------------------------------------------------------

async def _read_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path_arg = args.get("path", "")
    offset = args.get("offset")
    limit = args.get("limit")
    full = ctx.workspace.resolve_path(path_arg)
    try:
        text = full.read_text()
    except FileNotFoundError:
        return {"error": f"file not found: {full}"}
    except PermissionError as e:
        return {"error": f"permission denied: {e}"}
    if offset is not None or limit is not None:
        lines = text.splitlines()
        start = int(offset or 0)
        end = start + int(limit) if limit is not None else len(lines)
        text = "\n".join(lines[start:end])
    return {"path": str(full), "content": text, "bytes": len(text.encode())}


read_file_tool = register(Tool(
    name="read_file",
    schema={
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace. Supports offset/limit (line-based).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative or absolute path."},
                    "offset": {"type": "integer", "description": "Starting line (0-indexed)."},
                    "limit": {"type": "integer", "description": "Number of lines."},
                },
                "required": ["path"],
            },
        },
    },
    execute=_read_file,
))


# ---- write_file -------------------------------------------------------------

async def _write_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path_arg = args.get("path", "")
    content = args.get("content", "")
    full = ctx.workspace.resolve_path(path_arg)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return {
        "path": str(full),
        "bytes_written": len(content.encode()),
        "inside_workspace": ctx.workspace.is_inside(full),
    }


write_file_tool = register(Tool(
    name="write_file",
    schema={
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file (creates parent dirs, overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    execute=_write_file,
))
