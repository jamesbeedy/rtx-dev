"""Finish tool: worker calls this last to signal done."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import Tool, ToolContext, register


async def _finish(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    summary = (args.get("summary") or "").strip()
    out_dir_str = ctx.env.get("VLLM_AGENT_OUT_DIR")
    if not out_dir_str:
        return {"error": "VLLM_AGENT_OUT_DIR not set in agent environment"}
    out_dir = Path(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not summary:
        summary_text = "(worker called finish() with empty summary)"
        warning = "empty summary"
    else:
        summary_text = summary
        warning = None
    (out_dir / "summary.md").write_text(summary_text)
    result: dict[str, Any] = {"status": "finished", "summary_path": str(out_dir / "summary.md")}
    if warning:
        result["warning"] = warning
    return result


finish_tool = register(Tool(
    name="finish",
    schema={
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that the task is complete. Provide a 1-2 paragraph "
                           "summary of what you did, what you changed, and anything the "
                           "orchestrator should verify.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
    execute=_finish,
))
