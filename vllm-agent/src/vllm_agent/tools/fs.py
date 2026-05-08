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


# ---- edit_file --------------------------------------------------------------

async def _edit_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path_arg = args.get("path", "")
    old = args.get("old", "")
    new = args.get("new", "")
    replace_all = bool(args.get("replace_all", False))
    full = ctx.workspace.resolve_path(path_arg)
    try:
        text = full.read_text()
    except FileNotFoundError:
        return {"error": f"file not found: {full}"}
    if old not in text:
        return {"error": f"old string not found in {full}"}
    if not replace_all and text.count(old) > 1:
        return {"error": f"old string is not unique ({text.count(old)} occurrences); "
                         "set replace_all=true or provide more context"}
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    full.write_text(new_text)
    return {
        "path": str(full),
        "replacements": text.count(old) if replace_all else 1,
        "bytes_written": len(new_text.encode()),
    }


edit_file_tool = register(Tool(
    name="edit_file",
    schema={
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace `old` with `new` in a file. By default `old` "
                           "must be unique; set replace_all=true to replace every "
                           "occurrence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    execute=_edit_file,
))


# ---- grep -------------------------------------------------------------------

import re
import shutil
import subprocess


async def _grep(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    pattern = args.get("pattern", "")
    glob = args.get("glob")
    path_arg = args.get("path") or "."
    base = ctx.workspace.resolve_path(path_arg)

    # Prefer ripgrep if available; otherwise fall back to a Python walk.
    if shutil.which("rg"):
        cmd = ["rg", "--line-number", "--no-heading", "--color=never", pattern]
        if glob:
            cmd += ["--glob", glob]
        cmd.append(str(base))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        matches: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            try:
                p, ln, body = line.split(":", 2)
                matches.append({"path": p, "line": int(ln), "text": body})
            except ValueError:
                continue
        return {"matches": matches[:200], "tool": "rg"}

    # Python fallback.
    rx = re.compile(pattern)
    matches = []
    iterator = base.rglob(glob) if glob else base.rglob("*")
    for fp in iterator:
        if not fp.is_file():
            continue
        try:
            for i, line in enumerate(fp.read_text().splitlines(), start=1):
                if rx.search(line):
                    matches.append({"path": str(fp), "line": i, "text": line})
                    if len(matches) >= 200:
                        return {"matches": matches, "tool": "python"}
        except (UnicodeDecodeError, PermissionError):
            continue
    return {"matches": matches, "tool": "python"}


grep_tool = register(Tool(
    name="grep",
    schema={
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search the workspace for a regex pattern. Uses ripgrep if available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path":    {"type": "string", "description": "Subpath to search; default = workspace root."},
                    "glob":    {"type": "string", "description": "Optional glob filter, e.g. '*.py'."},
                },
                "required": ["pattern"],
            },
        },
    },
    execute=_grep,
))


# ---- glob -------------------------------------------------------------------

async def _glob(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    pattern = args.get("pattern", "")
    path_arg = args.get("path") or "."
    base = ctx.workspace.resolve_path(path_arg)
    paths = [str(p) for p in base.glob(pattern) if p.is_file()][:500]
    return {"paths": paths, "count": len(paths)}


glob_tool = register(Tool(
    name="glob",
    schema={
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files in the workspace by glob pattern (e.g. '**/*.py').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path":    {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    execute=_glob,
))
