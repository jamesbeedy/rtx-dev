#!/usr/bin/env python3
"""MCP stdio server wrapping a vLLM OpenAI-compatible endpoint.

Design philosophy — every generation tool has these two properties baked in,
non-negotiable:

  1. Web search is always available to the model (DuckDuckGo HTML, no API key).
     The model autonomously decides whether to use it; you don't toggle it.
  2. Generated content is always written to disk and only metadata returns.
     Claude's context never accumulates raw model output.

Tool surface (7 total):

  Utility:
    - health()              probe the vLLM endpoint
    - list_models()         list served models
    - verify_project(path)  smoke-test a Python project on disk

  Generation (all write to disk, all have web search):
    - ask(prompt, out_path)            single-turn Q&A, writes the answer
    - converse(messages, out_path)     multi-turn dialog, writes the final reply
    - scaffold(prompt, out_dir)        multi-file project generation
    - critique(prompt, draft, out_path) draft → corrected version

Environment:
    VLLM_BASE_URL        default http://127.0.0.1:8000
    VLLM_MODEL           default model id
    VLLM_API_KEY         optional Bearer auth
    VLLM_DEFAULT_SYSTEM  optional default system prompt for `ask`
    DDG_MIN_INTERVAL_S   seconds between DDG requests (default 1.5)
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

import httpx
import yaml
from mcp.server.fastmcp import FastMCP

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "")
VLLM_API_KEY = os.environ.get("VLLM_API_KEY", "")
DEFAULT_SYSTEM = os.environ.get(
    "VLLM_DEFAULT_SYSTEM",
    (
        "You are a senior engineer. Be concrete and terse. Prefer the standard library. "
        "Use type hints throughout. When you state a fact derived from a web_search "
        "result, include the URL in parentheses after the claim. "
        "Use web_search whenever you need facts you don't reliably know — current events, "
        "recent docs, exact APIs, version numbers."
    ),
)
_DDG_MIN_INTERVAL = float(os.environ.get("DDG_MIN_INTERVAL_S", "1.5"))

# import-name → PyPI distribution-name aliases for verify_project
_IMPORT_TO_DIST = {
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "pydantic_settings": "pydantic-settings",
    "sklearn": "scikit-learn",
    "kfp": "kfp",
}

mcp = FastMCP("vllm-inference")


# ---------------------------------------------------------------------------
# Helper: run a single-turn or multi-turn loop via vllm_agent.loop.run_loop
# with only web_search available. This is the new shared engine for
# ask/converse/critique/scaffold (replaces the local _generate).
# ---------------------------------------------------------------------------
import asyncio as _asyncio
from pathlib import Path as _Path

from vllm_agent.loop import LoopConfig as _LoopConfig, run_loop as _run_loop
from vllm_agent.tools import ToolContext as _ToolContext
from vllm_agent.tools import search as _search  # noqa: F401  registers web_search
from vllm_agent.transcript import Transcript as _Transcript
from vllm_agent.workspace import Workspace as _Workspace


async def _run_via_vllm_agent(
    msgs: list[dict[str, Any]],
    *,
    out_dir: Path,
    model: str | None,
    max_iterations: int,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    """Run a chat-completion + web_search loop via the shared agent runtime.

    Returns: {"answer", "iterations", "search_log"}.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace = _Workspace.resolve(None)
    ctx = _ToolContext(
        workspace=workspace,
        transcript=_Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )
    cfg = _LoopConfig(
        vllm_base_url=VLLM_BASE_URL,
        vllm_model=model or VLLM_MODEL,
        max_iterations=max_iterations,
        max_tokens=max_tokens,
        temperature=temperature,
        api_key=VLLM_API_KEY or None,
        tools_subset=["web_search"],
    )
    result = await _run_loop(msgs, ctx, cfg)
    search_log: list[dict[str, Any]] = []
    tpath = out_dir / "transcript.jsonl"
    if tpath.exists():
        for line in tpath.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "tool_call" and rec.get("tool") == "web_search":
                args = rec.get("args") or {}
                r = rec.get("result") or {}
                if "error" in r:
                    search_log.append({"query": args.get("query", ""), "error": r["error"]})
                else:
                    search_log.append({"query": args.get("query", ""),
                                       "n_results": len(r.get("results", []))})
    return {
        "answer": result.final_message_content or "",
        "iterations": result.iterations,
        "search_log": search_log,
    }


# =============================================================================
# Browser-fingerprint headers (rotated UA + minimal real headers)
# =============================================================================

_BROWSER_UAS = [
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _browser_headers(referer: str | None = None) -> dict[str, str]:
    """Minimal headers that pass anti-bot heuristics. Rotates UA per call."""
    h = {
        "User-Agent": random.choice(_BROWSER_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    }
    if referer:
        h["Referer"] = referer
    return h


# =============================================================================
# DuckDuckGo search (rate-limited, browser-headered)
# =============================================================================

_ddg_last_call: float = 0.0
_ddg_lock: asyncio.Lock | None = None


async def _ddg_throttle() -> None:
    """Block until DDG_MIN_INTERVAL_S has passed since the last call."""
    global _ddg_last_call, _ddg_lock
    if _ddg_lock is None:
        _ddg_lock = asyncio.Lock()
    async with _ddg_lock:
        elapsed = time.monotonic() - _ddg_last_call
        wait = _DDG_MIN_INTERVAL - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        _ddg_last_call = time.monotonic()


async def _ddg_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Hit DuckDuckGo HTML, parse with BeautifulSoup, return top results."""
    from bs4 import BeautifulSoup
    await _ddg_throttle()
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        r = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "us-en"},
            headers=_browser_headers(),
        )
        r.raise_for_status()
        html = r.text
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, str]] = []
    for div in soup.select("div.result")[:max_results]:
        a = div.select_one("a.result__a")
        snip = div.select_one("a.result__snippet, div.result__snippet")
        if a is None:
            continue
        url = a.get("href") or ""
        if url.startswith("/l/?") or url.startswith("//duckduckgo.com/l/"):
            from urllib.parse import urlparse, parse_qs, unquote
            qs = parse_qs(urlparse(url).query)
            if "uddg" in qs:
                url = unquote(qs["uddg"][0])
        out.append({
            "title": a.get_text(strip=True),
            "url": url,
            "snippet": snip.get_text(strip=True) if snip else "",
        })
    return out


# =============================================================================
# Core: chat-completion loop with web search tool always available
# =============================================================================

_WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the public web via DuckDuckGo and return up to N result "
            "snippets. Use whenever you need current information, exact API "
            "details, version numbers, or facts you are not confident about."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
            },
            "required": ["query"],
        },
    },
}


def _vllm_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if VLLM_API_KEY:
        h["Authorization"] = f"Bearer {VLLM_API_KEY}"
    return h


async def _generate(
    messages: list[dict[str, Any]],
    *,
    model: str | None,
    max_iterations: int,
    max_results: int,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    """Run the tool-calling loop until the model produces a final answer (no
    more tool_calls) or max_iterations is reached. Returns:
        {"messages", "answer", "iterations", "search_log"}.
    """
    msgs = [dict(m) for m in messages]
    search_log: list[dict[str, Any]] = []
    iterations = 0

    async with httpx.AsyncClient(timeout=600.0) as client:
        for i in range(max_iterations):
            iterations = i + 1
            r = await client.post(
                f"{VLLM_BASE_URL}/v1/chat/completions",
                headers=_vllm_headers(),
                json={
                    "model": model or VLLM_MODEL,
                    "messages": msgs,
                    "tools": [_WEB_SEARCH_SCHEMA],
                    "tool_choice": "auto",
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            msgs.append({
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": msg.get("tool_calls") or [],
            })
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return {
                    "messages": msgs,
                    "answer": msg.get("content") or "",
                    "iterations": iterations,
                    "search_log": search_log,
                }
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name == "web_search":
                    query = args.get("query", "")
                    try:
                        results = await _ddg_search(query, max_results)
                        content = json.dumps(results)
                        search_log.append({"query": query, "n_results": len(results)})
                    except Exception as e:
                        content = json.dumps({"error": f"{type(e).__name__}: {e}"})
                        search_log.append({"query": query, "error": str(e)})
                else:
                    content = json.dumps({"error": f"unknown tool: {name}"})
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": content,
                })

    return {
        "messages": msgs,
        "answer": msgs[-1].get("content") or "(max iterations reached without final answer)",
        "iterations": iterations,
        "search_log": search_log,
    }


def _strip_outer_fence(text: str) -> str:
    """If the entire response is wrapped in a single ``` fence, strip it."""
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1 and text.endswith("```"):
            return text[first_nl + 1:-3].rstrip()
    return text


_FILE_BLOCK = re.compile(
    r"^===\s*FILE:\s*(?P<path>.+?)\s*(?:===)?\s*\n"
    r"```[\w+-]*\s*\n"
    r"(?P<body>.*?)\n```",
    re.DOTALL | re.MULTILINE,
)


def _parse_file_blocks(text: str) -> list[tuple[str, str]]:
    return [(m.group("path").strip(), m.group("body")) for m in _FILE_BLOCK.finditer(text)]


def _safe_join(base: Path, rel: str) -> Path:
    candidate = (base / rel).resolve()
    base_resolved = base.resolve()
    if not candidate.is_relative_to(base_resolved):
        raise ValueError(f"Path {rel!r} would escape base dir {base_resolved}")
    return candidate


def _parse_and_write(content: str, base: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse FILE blocks from `content`, write each under `base`, return
    (written_records, warnings)."""
    written: list[dict[str, Any]] = []
    warnings: list[str] = []
    for rel, body in _parse_file_blocks(content):
        try:
            full = _safe_join(base, rel)
        except ValueError as e:
            warnings.append(str(e))
            continue
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body)
        written.append({"path": str(full), "bytes": full.stat().st_size})
    return written, warnings


def _write_answer(out_path: str, answer: str, search_log: list[dict[str, Any]],
                  iterations: int, include_log: bool) -> Path:
    p = Path(out_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = _strip_outer_fence(answer)
    if include_log:
        log_lines = ["", "---", f"_iterations: {iterations}_"]
        for e in search_log:
            if "error" in e:
                log_lines.append(f"_search: {e['query']!r} → ERROR: {e['error']}_")
            else:
                log_lines.append(f"_search: {e['query']!r} → {e['n_results']} results_")
        body = body + "\n" + "\n".join(log_lines) + "\n"
    p.write_text(body)
    return p


# =============================================================================
# Tools: utility
# =============================================================================

@mcp.tool()
async def health() -> dict[str, Any]:
    """Probe the vLLM endpoint and return basic status info."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{VLLM_BASE_URL}/health", headers=_vllm_headers())
            ok = r.status_code == 200
        except Exception as e:
            return {"ok": False, "endpoint": VLLM_BASE_URL, "error": str(e)}
    return {
        "ok": ok,
        "endpoint": VLLM_BASE_URL,
        "model": VLLM_MODEL or None,
        "default_system_set": bool(DEFAULT_SYSTEM),
        "ddg_min_interval_s": _DDG_MIN_INTERVAL,
    }


@mcp.tool()
async def list_models() -> list[str]:
    """List model ids served by the configured vLLM endpoint."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{VLLM_BASE_URL}/v1/models", headers=_vllm_headers())
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]


# =============================================================================
# Tools: generation (all write-to-disk + search-enabled)
# =============================================================================

@mcp.tool()
async def ask(
    prompt: str,
    out_path: str,
    system: str | None = None,
    model: str | None = None,
    max_iterations: int = 3,
    max_results: int = 5,
    max_tokens: int = 1500,
    temperature: float = 0.3,
    include_log: bool = False,
) -> dict[str, Any]:
    """Single-turn ask. The model has `web_search` available and the answer is
    written to `out_path` — only metadata returns to the caller.

    Returns: {"path", "bytes_written", "iterations", "search_log",
              "duration_s", "answer_preview"}.
    """
    sys_prompt = DEFAULT_SYSTEM if system is None else system
    msgs: list[dict[str, Any]] = []
    if sys_prompt:
        msgs.append({"role": "system", "content": sys_prompt})
    msgs.append({"role": "user", "content": prompt})

    p = _Path(out_path).expanduser()
    out_dir_ag = p.parent if p.parent != _Path("") else _Path.cwd()
    t0 = time.perf_counter()
    result = await _run_via_vllm_agent(
        msgs, out_dir=out_dir_ag, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature,
    )
    elapsed = time.perf_counter() - t0
    p_written = _write_answer(out_path, result["answer"], result["search_log"],
                              result["iterations"], include_log)
    return {
        "path": str(p_written),
        "bytes_written": p_written.stat().st_size,
        "iterations": result["iterations"],
        "search_log": result["search_log"],
        "duration_s": round(elapsed, 2),
        "answer_preview": result["answer"][:80],
    }


@mcp.tool()
async def converse(
    messages: list[dict[str, Any]],
    out_path: str,
    model: str | None = None,
    max_iterations: int = 3,
    max_results: int = 5,
    max_tokens: int = 1500,
    temperature: float = 0.3,
    include_log: bool = False,
) -> dict[str, Any]:
    """Multi-turn dialog. Pass an OpenAI-format messages list; the model has
    `web_search` available and the final assistant reply is written to `out_path`.

    Returns the same metadata shape as `ask`.
    """
    p = _Path(out_path).expanduser()
    out_dir_ag = p.parent if p.parent != _Path("") else _Path.cwd()
    t0 = time.perf_counter()
    result = await _run_via_vllm_agent(
        list(messages), out_dir=out_dir_ag, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature,
    )
    elapsed = time.perf_counter() - t0
    p_written = _write_answer(out_path, result["answer"], result["search_log"],
                              result["iterations"], include_log)
    return {
        "path": str(p_written),
        "bytes_written": p_written.stat().st_size,
        "iterations": result["iterations"],
        "search_log": result["search_log"],
        "duration_s": round(elapsed, 2),
        "answer_preview": result["answer"][:80],
    }


@mcp.tool()
async def scaffold(
    prompt: str,
    out_dir: str,
    system: str | None = None,
    model: str | None = None,
    max_iterations: int = 3,
    max_results: int = 5,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    minimize_search: bool = True,
    require_files: list[str] | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Multi-file project generation. Parses `=== FILE: relative/path === ...`
    blocks from the model output and writes each under `out_dir`. None of the
    generated content enters Claude's context.

    Args:
        minimize_search: When True (default), the system prompt strongly
            discourages web_search use — observed in practice that the model
            otherwise burns its token budget on searches before emitting code.
            Set False to revert to the encouraging prompt.
        require_files: Optional list of relative paths that must end up on disk.
            If any are missing after the first pass, scaffold automatically fires
            up to `max_retries` follow-up generations asking only for the
            missing files (uses the same out_dir, sys_prompt and model).
        max_retries: Cap on follow-up rounds for `require_files`. Default 2.

    Returns: {"out_dir", "files", "n_files", "iterations", "retries",
              "search_log", "duration_s", "warnings", "missing_required"}.
    """
    if system is not None:
        sys_prompt = system
    elif minimize_search:
        sys_prompt = (
            "You generate complete projects. Output ONLY file content blocks in this "
            "exact format, with no prose before, between, or after:\n\n"
            "=== FILE: relative/path ===\n```language\n<file content>\n```\n\n"
            "CRITICAL: web_search is available but you should NOT use it unless "
            "absolutely necessary — you already know the common APIs, frameworks, "
            "and license texts. Output FILE blocks immediately. Each search costs "
            "output tokens that would otherwise produce code."
        )
    else:
        sys_prompt = (
            "You generate complete projects. Output ONLY file content blocks in this "
            "exact format, with no prose before, between, or after:\n\n"
            "=== FILE: relative/path ===\n```language\n<file content>\n```\n\n"
            "You may use the `web_search` tool first if you need to confirm current "
            "API names, versions, or recent docs. After any searches, output ONLY the "
            "FILE blocks — no preamble, no commentary."
        )

    base = Path(out_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt},
    ]

    t0 = time.perf_counter()
    result = await _generate(
        msgs, model=model, max_iterations=max_iterations,
        max_results=max_results, max_tokens=max_tokens, temperature=temperature,
    )

    written, warnings = _parse_and_write(result["answer"], base)
    iterations_total = result["iterations"]
    search_log = list(result["search_log"])
    retries_used = 0

    # Auto-retry for missing required files.
    if require_files:
        required_set = {p.lstrip("./").rstrip("/") for p in require_files}
        for _ in range(max_retries):
            written_rels = {
                Path(w["path"]).relative_to(base).as_posix() for w in written
            }
            missing = sorted(required_set - written_rels)
            if not missing:
                break
            retries_used += 1
            retry_prompt = (
                "You generated a partial project. Output ONLY the FILE blocks for "
                "these MISSING paths, in the same `=== FILE: <path> ===` format. "
                "Do NOT regenerate files that already exist.\n\n"
                "Missing files:\n  - " + "\n  - ".join(missing) + "\n\n"
                "Original task (for context):\n"
                + (prompt[:1500] + (" ... [truncated]" if len(prompt) > 1500 else ""))
            )
            retry_result = await _generate(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": retry_prompt},
                ],
                model=model, max_iterations=max_iterations,
                max_results=max_results, max_tokens=max_tokens,
                temperature=temperature,
            )
            retry_written, retry_warnings = _parse_and_write(retry_result["answer"], base)
            written.extend(retry_written)
            warnings.extend(retry_warnings)
            iterations_total += retry_result["iterations"]
            search_log.extend(retry_result["search_log"])
            if not retry_written:
                # Avoid infinite loops if a retry produces nothing.
                warnings.append(f"Retry {retries_used} produced no FILE blocks; aborting.")
                break

    elapsed = time.perf_counter() - t0

    if not written:
        warnings.append("No FILE blocks parsed from model output; nothing written.")

    final_missing: list[str] = []
    if require_files:
        written_rels = {Path(w["path"]).relative_to(base).as_posix() for w in written}
        final_missing = sorted(
            {p.lstrip("./").rstrip("/") for p in require_files} - written_rels
        )

    return {
        "out_dir": str(base),
        "files": written,
        "n_files": len(written),
        "iterations": iterations_total,
        "retries": retries_used,
        "search_log": search_log,
        "duration_s": round(elapsed, 2),
        "warnings": warnings,
        "missing_required": final_missing,
    }


@mcp.tool()
async def critique(
    prompt: str,
    draft: str,
    out_path: str,
    model: str | None = None,
    max_iterations: int = 2,
    max_results: int = 4,
    max_tokens: int = 2000,
    temperature: float = 0.2,
    include_log: bool = False,
) -> dict[str, Any]:
    """Take an original task + a draft answer; produce a corrected version.
    The model has `web_search` available (useful for verifying API names,
    deprecations, version numbers). The corrected output is written to
    `out_path` — only metadata returns.

    Returns the same metadata shape as `ask`.
    """
    system = (
        "You are a strict senior code reviewer. Given an original task and a draft "
        "answer, identify bugs, missing edge cases, type errors, and style issues, "
        "then output the CORRECTED VERSION ONLY — not the critique. Use the "
        "`web_search` tool to verify any API names, version-specific behavior, or "
        "deprecations you are unsure about. Keep the same shape (same files, same "
        "names) unless correctness requires otherwise. Do not add prose."
    )
    user = (
        f"=== ORIGINAL TASK ===\n{prompt}\n\n"
        f"=== DRAFT TO REVIEW ===\n{draft}\n\n"
        f"=== YOUR TASK ===\nProduce the corrected version. Output only the "
        f"corrected artifact (code, files, etc.), no commentary."
    )
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    t0 = time.perf_counter()
    result = await _generate(
        msgs, model=model, max_iterations=max_iterations,
        max_results=max_results, max_tokens=max_tokens, temperature=temperature,
    )
    elapsed = time.perf_counter() - t0
    p = _write_answer(out_path, result["answer"], result["search_log"],
                      result["iterations"], include_log)
    return {
        "path": str(p),
        "bytes_written": p.stat().st_size,
        "iterations": result["iterations"],
        "search_log": result["search_log"],
        "duration_s": round(elapsed, 2),
        "answer_preview": result["answer"][:80],
    }


# =============================================================================
# Tools: project verification (returns metadata; never echoes source)
# =============================================================================

def _summary(checks: list[dict[str, Any]]) -> str:
    n_ok = sum(1 for c in checks if c["ok"])
    return f"{n_ok}/{len(checks)} checks passed"


def _trim(s: str, n: int = 240) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n // 2] + " ... " + s[-n // 2:]


def _verify_charm(base: Path, charmcraft_path: Path,
                   checks: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
    """Charm-layout-specific checks: charmcraft.yaml validity + py_compile.
    Skips `package_imports` / `cli_help` / `imports_declared` because charms
    aren't installable Python packages or CLI tools."""
    name = ""
    try:
        with open(charmcraft_path) as f:
            cfg = yaml.safe_load(f)
        if not isinstance(cfg, dict):
            raise ValueError("charmcraft.yaml is not a YAML mapping")
        if cfg.get("type") != "charm":
            raise ValueError(f"type is {cfg.get('type')!r}, expected 'charm'")
        name = cfg.get("name", "")
        if not name:
            raise ValueError("missing required field 'name'")
        src_charm = base / "src" / "charm.py"
        if not src_charm.is_file():
            raise ValueError("missing src/charm.py")
        is_subordinate = bool(cfg.get("subordinate"))
        detail = (f"name={name!r}, type=charm"
                  + (", subordinate=true" if is_subordinate else "")
                  + ", src/charm.py present")
        checks.append({"name": "charmcraft_yaml_valid", "ok": True, "detail": detail})
    except Exception as e:
        checks.append({"name": "charmcraft_yaml_valid", "ok": False, "detail": _trim(str(e))})
        return {"path": str(base), "kind": "charm", "ok": False,
                "checks": checks, "summary": _summary(checks)}

    py_files = [p for p in base.rglob("*.py")
                if ".verify_venv" not in p.parts and ".venv" not in p.parts]
    compile_errs: list[str] = []
    for pf in py_files:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(pf)],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            compile_errs.append(f"{pf.relative_to(base)}: {r.stderr.strip()[:120]}")
    checks.append({
        "name": "py_compile_all",
        "ok": not compile_errs,
        "detail": (f"{len(py_files)} files OK" if not compile_errs
                   else _trim("; ".join(compile_errs[:3]))),
    })

    # NOTE: `charmcraft analyze` only validates packed .charm files, not source
    # trees. `charmcraft pack` actually builds (slow, downloads bases). For a
    # quick source-tree smoke test, charmcraft_yaml_valid + py_compile_all is
    # the right level. Run `charmcraft pack` separately when you want a real build.

    return {
        "path": str(base),
        "kind": "charm",
        "name": name,
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "summary": _summary(checks),
    }


@mcp.tool()
def verify_project(
    path: str,
    isolated: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run smoke tests on a project at `path`. Auto-detects layout:

    - **Charm** (charmcraft.yaml present): charmcraft_yaml_valid, py_compile_all,
      and (if `charmcraft` is on PATH) charmcraft_analyze. Skips package/CLI
      checks that don't apply to charms.
    - **Python package** (pyproject.toml present): pyproject_valid, py_compile_all,
      imports_declared, package_imports, cli_help. `isolated=True` runs the
      import + CLI checks inside a fresh `uv venv` with `pip install -e .`.

    Returns pass/fail metadata with truncated error details. Source code never
    enters Claude's context.
    """
    base = Path(path).expanduser().resolve()
    checks: list[dict[str, Any]] = []

    if not base.is_dir():
        checks.append({"name": "path_exists", "ok": False, "detail": f"not a directory: {base}"})
        return {"path": str(base), "ok": False, "checks": checks, "summary": _summary(checks)}

    charmcraft_path = base / "charmcraft.yaml"
    pyproject = base / "pyproject.toml"

    if charmcraft_path.is_file():
        checks.append({"name": "path_exists", "ok": True, "detail": str(base)})
        return _verify_charm(base, charmcraft_path, checks, timeout)

    if not pyproject.is_file():
        checks.append({"name": "path_exists", "ok": False,
                       "detail": "neither charmcraft.yaml nor pyproject.toml present"})
        return {"path": str(base), "ok": False, "checks": checks, "summary": _summary(checks)}
    checks.append({"name": "path_exists", "ok": True, "detail": str(base)})

    try:
        with open(pyproject, "rb") as f:
            cfg = tomllib.load(f)
        proj = cfg.get("project", {})
        if not proj:
            raise ValueError("pyproject.toml missing [project] table")
        proj_name = proj.get("name", "")
        scripts = proj.get("scripts", {})
        pkg_name = ""
        if scripts:
            entry = next(iter(scripts.values()))
            pkg_name = entry.split(":")[0].split(".")[0]
        if not pkg_name:
            pkg_name = (proj_name or "").replace("-", "_")
        if not pkg_name:
            raise ValueError("could not derive package name from pyproject.toml")
        checks.append({
            "name": "pyproject_valid", "ok": True,
            "detail": f"project={proj_name!r}, package={pkg_name!r}",
        })
    except Exception as e:
        checks.append({"name": "pyproject_valid", "ok": False, "detail": _trim(str(e))})
        return {"path": str(base), "ok": False, "checks": checks, "summary": _summary(checks)}

    py_files = [p for p in base.rglob("*.py")
                if ".verify_venv" not in p.parts and ".venv" not in p.parts]
    compile_errs: list[str] = []
    for pf in py_files:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(pf)],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            compile_errs.append(f"{pf.relative_to(base)}: {r.stderr.strip()[:120]}")
    checks.append({
        "name": "py_compile_all",
        "ok": not compile_errs,
        "detail": (f"{len(py_files)} files OK" if not compile_errs
                   else _trim("; ".join(compile_errs[:3]))),
    })
    if compile_errs:
        return {"path": str(base), "ok": False, "package": pkg_name,
                "checks": checks, "summary": _summary(checks)}

    imports: set[str] = set()
    for pf in py_files:
        try:
            tree = ast.parse(pf.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module.split(".")[0])
    declared_dists: set[str] = set()
    for dep in proj.get("dependencies", []):
        name = re.split(r"[<>=!~;\s\[]", dep, 1)[0].strip()
        if name:
            declared_dists.add(name.lower())
    stdlib = set(sys.stdlib_module_names)
    own_pkg_names = {pkg_name, (proj_name or "").replace("-", "_")}
    undeclared: list[str] = []
    for imp in sorted(imports):
        if imp in stdlib or imp in own_pkg_names:
            continue
        dist = _IMPORT_TO_DIST.get(imp, imp).lower()
        if dist not in declared_dists:
            undeclared.append(imp)
    checks.append({
        "name": "imports_declared",
        "ok": not undeclared,
        "detail": "ok" if not undeclared else f"undeclared imports: {undeclared}",
    })

    if isolated:
        venv_dir = base / ".verify_venv"
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        if shutil.which("uv") is None:
            checks.append({"name": "isolated_setup", "ok": False, "detail": "uv not found in PATH"})
            return {"path": str(base), "ok": False, "package": pkg_name,
                    "checks": checks, "summary": _summary(checks)}
        try:
            subprocess.run(["uv", "venv", str(venv_dir)],
                           check=True, capture_output=True, text=True, timeout=60)
            python_bin = str(venv_dir / "bin" / "python")
            install = subprocess.run(
                ["uv", "pip", "install", "--python", python_bin, "-e", str(base)],
                check=True, capture_output=True, text=True, timeout=180,
            )
            checks.append({"name": "isolated_setup", "ok": True,
                           "detail": _trim(install.stderr.strip().splitlines()[-1] if install.stderr else "ok")})
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "")[-300:]
            checks.append({"name": "isolated_setup", "ok": False, "detail": _trim(stderr)})
            return {"path": str(base), "ok": False, "package": pkg_name,
                    "checks": checks, "summary": _summary(checks)}
    else:
        python_bin = sys.executable

    env = {**os.environ}
    if not isolated:
        env["PYTHONPATH"] = str(base) + (":" + env["PYTHONPATH"] if "PYTHONPATH" in env else "")

    r = subprocess.run(
        [python_bin, "-c", f"import {pkg_name}"],
        capture_output=True, text=True, cwd=str(base), env=env, timeout=timeout,
    )
    checks.append({
        "name": "package_imports",
        "ok": r.returncode == 0,
        "detail": "ok" if r.returncode == 0 else _trim(r.stderr or r.stdout),
    })

    r = subprocess.run(
        [python_bin, "-m", pkg_name, "--help"],
        capture_output=True, text=True, cwd=str(base), env=env, timeout=timeout,
    )
    cli_ok = (r.returncode in (0, 2) and
              ("usage:" in r.stdout.lower() or "usage:" in r.stderr.lower()))
    checks.append({
        "name": "cli_help",
        "ok": cli_ok,
        "detail": "ok" if cli_ok else _trim(r.stderr or r.stdout),
    })

    return {
        "path": str(base),
        "package": pkg_name,
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "summary": _summary(checks),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
