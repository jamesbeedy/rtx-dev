"""Shell tool: unrestricted bash. Local mode requires VLLM_AGENT_LOCAL_BASH=1."""
from __future__ import annotations

import asyncio
from typing import Any

from . import Tool, ToolContext, register


async def _bash(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    command = args.get("command", "")
    cwd = args.get("cwd") or str(ctx.workspace.root)
    timeout_s = float(args.get("timeout_s", 60))

    # Safety gate: in local mode require explicit opt-in.
    if ctx.env.get("VLLM_AGENT_MODE") == "local" and ctx.env.get("VLLM_AGENT_LOCAL_BASH") != "1":
        return {"error": "Local-mode bash is disabled. Set VLLM_AGENT_LOCAL_BASH=1 "
                         "in the agent environment to enable it."}

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"error": f"timeout after {timeout_s}s", "exit_code": -1,
                "stdout": "", "stderr": ""}
    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace")[:64_000],
        "stderr": stderr.decode(errors="replace")[:16_000],
    }


bash_tool = register(Tool(
    name="bash",
    schema={
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command. Unrestricted (no command allowlist). "
                           "cwd defaults to the workspace root. timeout_s defaults to 60.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command":   {"type": "string"},
                    "cwd":       {"type": "string"},
                    "timeout_s": {"type": "number"},
                },
                "required": ["command"],
            },
        },
    },
    execute=_bash,
))
