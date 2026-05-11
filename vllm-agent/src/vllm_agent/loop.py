"""The agent tool-call loop."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9]*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Strip markdown code fences from model output — applied recursively until stable."""
    while True:
        m = _FENCE_RE.match(text.strip())
        if not m:
            break
        text = m.group(1)
    return text.strip()

from .tools import WORKER_TOOLS, ToolContext


@dataclass
class LoopConfig:
    vllm_base_url: str
    vllm_model: str
    max_iterations: int = 30
    max_tokens: int = 4096
    temperature: float = 0.2
    api_key: str | None = None
    request_timeout_s: float = 600.0
    tools_subset: list[str] | None = None


@dataclass
class LoopResult:
    messages: list[dict[str, Any]]
    iterations: int
    status: str   # "ok" | "max_iterations" | "error"
    final_message_content: str | None = None
    tool_calls_by_name: Counter = field(default_factory=Counter)
    error: str | None = None


def _vllm_headers(api_key: str | None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _tools_schema(subset: list[str] | None = None) -> list[dict[str, Any]]:
    if subset is None:
        return [t.schema for t in WORKER_TOOLS.values()]
    return [t.schema for name, t in WORKER_TOOLS.items() if name in subset]


async def run_loop(
    messages: list[dict[str, Any]],
    ctx: ToolContext,
    cfg: LoopConfig,
) -> LoopResult:
    msgs = [dict(m) for m in messages]
    tool_counts: Counter = Counter()
    iterations = 0
    final_content: str | None = None

    async with httpx.AsyncClient(timeout=cfg.request_timeout_s) as client:
        for i in range(cfg.max_iterations):
            iterations = i + 1
            attempt = 0
            while True:
                try:
                    r = await client.post(
                        f"{cfg.vllm_base_url}/v1/chat/completions",
                        headers=_vllm_headers(cfg.api_key),
                        json={
                            "model": cfg.vllm_model,
                            "messages": msgs,
                            "tools": _tools_schema(cfg.tools_subset),
                            "tool_choice": "auto",
                            "max_tokens": cfg.max_tokens,
                            "temperature": cfg.temperature,
                        },
                    )
                    r.raise_for_status()
                    break
                except httpx.HTTPError as e:
                    if attempt >= 1:
                        return LoopResult(messages=msgs, iterations=iterations,
                                          status="error",
                                          tool_calls_by_name=tool_counts,
                                          error=f"vLLM HTTP error: {type(e).__name__}: {e}")
                    attempt += 1
                    import asyncio
                    await asyncio.sleep(1.0)

            data = r.json()
            msg = data["choices"][0]["message"]
            assistant_msg = {"role": "assistant",
                             "content": msg.get("content"),
                             "tool_calls": msg.get("tool_calls") or []}
            msgs.append(assistant_msg)
            ctx.transcript.record_message("assistant", assistant_msg)

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                final_content = msg.get("content")
                return LoopResult(messages=msgs, iterations=iterations,
                                  status="ok", final_message_content=final_content,
                                  tool_calls_by_name=tool_counts)
            for tc in tool_calls:
                fn = (tc.get("function") or {})
                name = fn.get("name", "")
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError:
                    try:
                        args = json.loads(_strip_fences(raw))
                    except json.JSONDecodeError:
                        args = {}
                tool_counts[name] += 1
                tool = WORKER_TOOLS.get(name)
                if tool is None:
                    result = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = await tool.execute(args, ctx)
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}
                ctx.transcript.record_tool_call(name, args, result)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                # Worker called finish → end loop early.
                if name == "finish" and result.get("status") == "finished":
                    return LoopResult(messages=msgs, iterations=iterations,
                                      status="ok",
                                      final_message_content=result.get("summary_path"),
                                      tool_calls_by_name=tool_counts)

    return LoopResult(messages=msgs, iterations=iterations,
                      status="max_iterations",
                      tool_calls_by_name=tool_counts)
