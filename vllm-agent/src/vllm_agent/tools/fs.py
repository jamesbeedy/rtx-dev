"""Filesystem tools: read, write, edit, grep, glob."""
from __future__ import annotations

from typing import Any

from . import Tool, ToolContext, register


# ---- read_file --------------------------------------------------------------

# Hard cap on returned content per call. ~5k tokens at 3 chars/token.
# Worker must use offset/limit to read further chunks of large files.
READ_FILE_MAX_BYTES = 16_384

# After this many read_file calls on a single resolved path within one run,
# refuse further reads and steer the worker to grep instead. Prevents the
# "walk the same file at increasing offsets until the window dies" pattern.
READ_FILE_PER_PATH_LIMIT = 3


def _truncate_to_bytes(lines: list[str], max_bytes: int) -> tuple[str, int]:
    """Concatenate `lines` (with newlines) up to `max_bytes`. Returns (text, n_lines_used)."""
    out: list[str] = []
    total = 0
    for i, line in enumerate(lines):
        chunk = line if i == len(lines) - 1 else line + "\n"
        b = len(chunk.encode())
        if total + b > max_bytes:
            break
        out.append(chunk)
        total += b
    return "".join(out), len(out)


async def _read_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path_arg = args.get("path", "")
    offset = args.get("offset")
    limit = args.get("limit")
    max_bytes = int(args.get("max_bytes") or READ_FILE_MAX_BYTES)
    full = ctx.workspace.resolve_path(path_arg)
    key = str(full)
    prior = ctx.read_counts.get(key, 0)
    if prior >= READ_FILE_PER_PATH_LIMIT:
        return {
            "error": (
                f"read_file budget exhausted for {full}: already read "
                f"{prior} times this run. Switch strategy — use grep to "
                "find the exact lines you need, or finish() with what you "
                "have. Re-reading the same file at new offsets is the "
                "single biggest cause of context exhaustion."
            ),
            "path": key,
            "reads_so_far": prior,
        }
    ctx.read_counts[key] = prior + 1
    try:
        full_text = full.read_text()
    except FileNotFoundError:
        return {"error": f"file not found: {full}"}
    except PermissionError as e:
        return {"error": f"permission denied: {e}"}

    all_lines = full_text.splitlines()
    total_lines = len(all_lines)
    total_bytes = len(full_text.encode())

    start = int(offset or 0)
    whole_file = offset is None and limit is None
    end = start + int(limit) if limit is not None else total_lines
    sliced = all_lines[start:end]

    # When the caller didn't slice and the file fits the budget, return the
    # raw text verbatim (preserves trailing newlines etc.).
    if whole_file and total_bytes <= max_bytes:
        sliced_text = full_text
        truncated = False
        next_offset: int | None = None
    else:
        sliced_text = "\n".join(sliced)
        truncated = False
        next_offset = None
        if len(sliced_text.encode()) > max_bytes:
            sliced_text, n_used = _truncate_to_bytes(sliced, max_bytes)
            truncated = True
            next_offset = start + n_used

    result: dict[str, Any] = {
        "path": str(full),
        "content": sliced_text,
        "bytes": len(sliced_text.encode()),
        "total_bytes": total_bytes,
        "total_lines": total_lines,
        "offset": start,
        "lines_returned": len(sliced_text.splitlines()),
        "truncated": truncated,
    }
    if truncated:
        result["next_offset"] = next_offset
        result["hint"] = (
            f"File truncated at {max_bytes} bytes. Call read_file again with "
            f"offset={next_offset} to continue, or use grep to find the section "
            f"you need. Do NOT request the whole file at once."
        )
    return result


read_file_tool = register(Tool(
    name="read_file",
    schema={
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file from the workspace, line-based with offset/limit. "
                f"Output is capped at {READ_FILE_MAX_BYTES} bytes per call to "
                "protect the context window; if `truncated=true` in the result, "
                "use `next_offset` for the next chunk. For very large files, "
                "prefer `grep` to locate the region first, then read with "
                "offset/limit around the match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative or absolute path."},
                    "offset": {"type": "integer", "description": "Starting line (0-indexed)."},
                    "limit": {"type": "integer", "description": "Max number of lines to return."},
                    "max_bytes": {
                        "type": "integer",
                        "description": (
                            f"Override the per-call byte cap (default {READ_FILE_MAX_BYTES}). "
                            "Raising this risks blowing the context window."
                        ),
                    },
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
