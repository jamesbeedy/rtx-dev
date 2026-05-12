"""The agent tool-call loop."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
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


# Per-tool-result cap on what lands back in the context. Larger results are
# spilled to disk; the in-context payload only carries a head + a pointer.
TOOL_RESULT_INLINE_MAX_BYTES = 8_192
TOOL_RESULT_HEAD_BYTES = 2_048

# Context window (chars) and the fraction at which we tell the worker to wrap
# up. 32k tokens * 4 chars/token = 131072 chars total; reserve max_tokens for
# the next completion. We act at 80% of the remainder.
CONTEXT_WINDOW_CHARS = 131_072
CONTEXT_SOFT_LIMIT_FRACTION = 0.80


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
    status: str   # "ok" | "max_iterations" | "error" | "context_exhausted"
    final_message_content: str | None = None
    tool_calls_by_name: Counter = field(default_factory=Counter)
    error: str | None = None


def _msgs_char_len(msgs: list[dict[str, Any]]) -> int:
    """Approximate in-context size of the message list (chars ≈ 4 * tokens)."""
    total = 0
    for m in msgs:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif c is not None:
            total += len(json.dumps(c, ensure_ascii=False))
        tcs = m.get("tool_calls")
        if tcs:
            total += len(json.dumps(tcs, ensure_ascii=False))
    return total


def _spill_tool_result(
    result: dict[str, Any],
    tool_name: str,
    call_idx: int,
    out_dir: Path,
) -> dict[str, Any]:
    """If `result` JSON exceeds the inline cap, write the full payload to
    `out_dir/tool_outputs/<idx>-<tool>.json` and return a trimmed result with
    a `stored_at` pointer. Otherwise return the result unchanged."""
    body = json.dumps(result, ensure_ascii=False)
    if len(body.encode()) <= TOOL_RESULT_INLINE_MAX_BYTES:
        return result
    spill_dir = out_dir / "tool_outputs"
    spill_dir.mkdir(parents=True, exist_ok=True)
    path = spill_dir / f"{call_idx:04d}-{tool_name}.json"
    path.write_text(body)
    head = body[:TOOL_RESULT_HEAD_BYTES]
    return {
        "_spilled": True,
        "tool": tool_name,
        "stored_at": str(path),
        "total_bytes": len(body.encode()),
        "head": head,
        "hint": (
            f"Result too large to inline ({len(body.encode())} bytes). Full "
            f"JSON written to {path}. Read it with read_file(offset=, limit=) "
            "if you need more than the head shown above. Prefer narrowing the "
            "next call (grep first, smaller limit) instead of re-running this "
            "one."
        ),
    }


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

    out_dir = Path(ctx.env.get("VLLM_AGENT_OUT_DIR") or ".")
    soft_limit = int((CONTEXT_WINDOW_CHARS - cfg.max_tokens * 4) * CONTEXT_SOFT_LIMIT_FRACTION)
    hard_limit = CONTEXT_WINDOW_CHARS - cfg.max_tokens * 4
    nudged = False
    tool_call_seq = 0

    async with httpx.AsyncClient(timeout=cfg.request_timeout_s) as client:
        for i in range(cfg.max_iterations):
            iterations = i + 1

            # Hard ceiling: if msgs already exceed what vLLM can accept, bail
            # cleanly instead of letting the HTTP call 400 (which the retry
            # path mis-classifies as a transient error).
            cur = _msgs_char_len(msgs)
            if cur >= hard_limit:
                return LoopResult(
                    messages=msgs, iterations=iterations,
                    status="context_exhausted",
                    tool_calls_by_name=tool_counts,
                    error=(f"context budget exceeded: {cur} chars >= "
                           f"{hard_limit} (32k * 4 - max_tokens*4). "
                           "Worker did not call finish() in time."),
                )

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
                    # Surface context-overflow as its own status. vLLM returns
                    # 400 with "maximum context length" in the body when the
                    # prompt itself is too big; retrying is pointless.
                    body = ""
                    if isinstance(e, httpx.HTTPStatusError):
                        try:
                            body = e.response.text
                        except Exception:
                            body = ""
                    if "maximum context length" in body or "context length" in body.lower():
                        return LoopResult(
                            messages=msgs, iterations=iterations,
                            status="context_exhausted",
                            tool_calls_by_name=tool_counts,
                            error=f"vLLM rejected request: {body[:500]}",
                        )
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
                tool_call_seq += 1
                tool = WORKER_TOOLS.get(name)
                if tool is None:
                    result = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = await tool.execute(args, ctx)
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}
                # Transcript gets the full result on disk. The in-context copy
                # may be trimmed if oversized.
                ctx.transcript.record_tool_call(name, args, result)
                inline = _spill_tool_result(result, name, tool_call_seq, out_dir)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": json.dumps(inline, ensure_ascii=False),
                })
                # Worker called finish → end loop early.
                if name == "finish" and result.get("status") == "finished":
                    return LoopResult(messages=msgs, iterations=iterations,
                                      status="ok",
                                      final_message_content=result.get("summary_path"),
                                      tool_calls_by_name=tool_counts)

            # Soft-limit nudge: once we've crossed 80% of the usable window,
            # inject a one-shot user-turn telling the worker to wrap up. We
            # only do this once per run so the nudge can't loop on itself.
            if not nudged and _msgs_char_len(msgs) >= soft_limit:
                nudged = True
                nudge = (
                    "SYSTEM NOTICE — CONTEXT BUDGET WARNING: the conversation "
                    "is approaching the 32k context window. Stop reading new "
                    "files. Either (a) call finish() with what you have plus "
                    "an honest note about what's incomplete, or (b) make at "
                    "most one or two more small, targeted tool calls and then "
                    "finish(). Do NOT request another large file."
                )
                msgs.append({"role": "user", "content": nudge})
                ctx.transcript.record_message("user", nudge)

    return LoopResult(messages=msgs, iterations=iterations,
                      status="max_iterations",
                      tool_calls_by_name=tool_counts)
