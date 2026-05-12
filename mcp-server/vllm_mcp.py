#!/usr/bin/env python3
"""MCP stdio server wrapping a vLLM OpenAI-compatible endpoint.

Design philosophy — every generation tool has these two properties baked in,
non-negotiable:

  1. Web search is always available to the model (DuckDuckGo HTML, no API key).
     The model autonomously decides whether to use it; you don't toggle it.
  2. Generated content is always written to disk and only metadata returns.
     Claude's context never accumulates raw model output.

Tool surface (14 total):

  Utility:
    - health()              probe the vLLM endpoint
    - list_models()         list served models
    - verify_project(path)  smoke-test a Python project on disk
    - list_skills()         list discoverable agent skills (project/user/superpowers)

  Generation (delegate to vllm_agent.loop with web_search-only palette;
   write to disk, return only metadata):
    - ask(prompt, out_path)             single-turn Q&A, writes the answer
    - converse(messages, out_path)      multi-turn dialog, writes the final reply
    - scaffold(prompt, out_dir)         multi-file project generation
    - critique(prompt, draft, out_path) draft → corrected version

  Agent dispatch (delegate to vllm_agent.api; mode='local' runs in-process,
   mode='remote' POSTs to VLLM_AGENT_URL inside the VM):
    - agent_run(task, ...)              one-shot coding-agent task
    - agent_session_start(goal, ...)    start a long-running session
    - agent_session_step(session_id)    run one step
    - agent_session_status(session_id)  read session state
    - agent_session_stop(session_id)    stop a session
    - agent_run_artifacts(out_dir, ...) read back artifacts of a completed run

Environment:
    VLLM_BASE_URL        default http://127.0.0.1:8000
    VLLM_MODEL           default model id
    VLLM_API_KEY         optional Bearer auth
    VLLM_DEFAULT_SYSTEM  optional default system prompt for `ask`
    DDG_MIN_INTERVAL_S   reported in health() output; actual DDG throttling
                         lives in vllm-agent (default 1.5)
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from dataclasses import asdict
from typing import Any

import httpx
import yaml
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

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
VLLM_AGENT_URL = os.environ.get("VLLM_AGENT_URL", "")  # e.g. http://10.x.y.z:8088
VLLM_AGENT_API_KEY = os.environ.get("VLLM_AGENT_API_KEY", "")

# Allowlist of env vars forwarded to the worker via env_overlay (per-request).
# Keep this list small and intentional — anything added here is sent on every
# agent_run / agent_session_start call.
_ENV_OVERLAY_ALLOWLIST = ("GITHUB_TOKEN", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL")


def _build_env_overlay() -> dict[str, str]:
    """Build env_overlay from the MCP server's own environment.
    Read at call time so .mcp.json env changes apply on next dispatch
    without restarting the MCP server (FastMCP typically reloads on host
    restart, but reading lazily costs nothing)."""
    out: dict[str, str] = {}
    for key in _ENV_OVERLAY_ALLOWLIST:
        val = os.environ.get(key)
        if val:
            out[key] = val
    return out


def _agent_headers() -> dict[str, str]:
    """Authorization header for VM-side vllm-agent calls. Empty if no key set."""
    if VLLM_AGENT_API_KEY:
        return {"Authorization": f"Bearer {VLLM_AGENT_API_KEY}"}
    return {}

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
from pathlib import Path as _Path

from vllm_agent.api import (
    AgentRunRequest as _AgentRunRequest,
    AgentSessionStartRequest as _AgentSessionStartRequest,
    agent_run as _agent_run_local,
    agent_session_start as _ass_local,
    agent_session_step as _aststep_local,
    agent_session_status as _aststatus_local,
    agent_session_stop as _aststop_local,
)
from vllm_agent.loop import LoopConfig as _LoopConfig, run_loop as _run_loop
from vllm_agent.skills import SkillLoader as _SkillLoader, SkillNotFound as _SkillNotFound
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


async def _http_agent(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    params: dict | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    if not VLLM_AGENT_URL:
        return {"status": "error", "error": "VLLM_AGENT_URL not set"}
    url = f"{VLLM_AGENT_URL}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        if method == "GET":
            r = await client.get(url, params=params, headers=_agent_headers())
        else:
            r = await client.post(url, json=body or {}, headers=_agent_headers())
    if r.status_code != 200:
        return {"status": "error", "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    return r.json()


async def _agent_run_remote(req: _AgentRunRequest) -> dict[str, Any]:
    """POST the request to the VM's vllm-agent serve endpoint."""
    if not VLLM_AGENT_URL:
        return {"status": "error",
                "error": "VLLM_AGENT_URL not set; cannot use mode=remote"}
    body = {
        "task": req.task, "skill": req.skill, "mode": "remote",
        "workdir": req.workdir, "out_dir": req.out_dir, "model": req.model,
        "max_iterations": req.max_iterations, "max_tokens": req.max_tokens,
        "temperature": req.temperature, "timeout_s": req.timeout_s,
        "extra_context": req.extra_context, "skill_content": req.skill_content,
        "env_overlay": _build_env_overlay() or None,
    }
    return await _http_agent("POST", "/run", body=body, timeout=float(req.timeout_s + 30))


async def _http_session_start(body: dict) -> dict[str, Any]:
    return await _http_agent("POST", "/session", body=body, timeout=30.0)


async def _http_session_step(session_id: str, body: dict) -> dict[str, Any]:
    return await _http_agent("POST", f"/session/{session_id}/step", body=body, timeout=1800.0)


async def _http_session_status(session_id: str) -> dict[str, Any]:
    return await _http_agent("GET", f"/session/{session_id}", timeout=10.0)


async def _http_session_stop(session_id: str) -> dict[str, Any]:
    return await _http_agent("POST", f"/session/{session_id}/stop", timeout=10.0)


def _vllm_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if VLLM_API_KEY:
        h["Authorization"] = f"Bearer {VLLM_API_KEY}"
    return h


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

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True
))
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


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True
))
async def list_models() -> list[str]:
    """List model ids served by the configured vLLM endpoint."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{VLLM_BASE_URL}/v1/models", headers=_vllm_headers())
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]


# =============================================================================
# Tools: generation (all write-to-disk + search-enabled)
# =============================================================================

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True
))
async def ask(
    prompt: str,
    out_path: str,
    system: str | None = None,
    model: str | None = None,
    max_iterations: int = 3,
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
        "answer_preview": result["answer"][:200],
    }


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True
))
async def converse(
    messages: list[dict[str, Any]],
    out_path: str,
    model: str | None = None,
    max_iterations: int = 3,
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
        "answer_preview": result["answer"][:200],
    }


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True
))
async def scaffold(
    prompt: str,
    out_dir: str,
    system: str | None = None,
    model: str | None = None,
    max_iterations: int = 3,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    minimize_search: bool = True,
    require_files: list[str] | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Multi-file project generation. (See module docstring for details.)

    Note: `max_results` is accepted for backward compatibility but is no longer
    used — web_search uses its built-in default (5).
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

    base = _Path(out_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt},
    ]
    t0 = time.perf_counter()
    result_dict = await _run_via_vllm_agent(
        msgs, out_dir=base, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature,
    )
    written, warnings = _parse_and_write(result_dict["answer"], base)
    iterations_total = result_dict["iterations"]
    search_log = list(result_dict["search_log"])
    retries_used = 0

    if require_files:
        required_set = {p.lstrip("./").rstrip("/") for p in require_files}
        for _ in range(max_retries):
            written_rels = {Path(w["path"]).relative_to(base).as_posix() for w in written}
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
            retry_result = await _run_via_vllm_agent(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": retry_prompt},
                ],
                out_dir=base, model=model,
                max_iterations=max_iterations,
                max_tokens=max_tokens, temperature=temperature,
            )
            retry_written, retry_warnings = _parse_and_write(retry_result["answer"], base)
            written.extend(retry_written)
            warnings.extend(retry_warnings)
            iterations_total += retry_result["iterations"]
            search_log.extend(retry_result["search_log"])
            if not retry_written:
                warnings.append(f"Retry {retries_used} produced no FILE blocks; aborting.")
                break

    elapsed = time.perf_counter() - t0

    if not written:
        warnings.append("No FILE blocks parsed from model output; nothing written.")

    final_missing: list[str] = []
    if require_files:
        written_rels = {Path(w["path"]).relative_to(base).as_posix() for w in written}
        final_missing = sorted({p.lstrip("./").rstrip("/") for p in require_files} - written_rels)

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


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True
))
async def critique(
    prompt: str,
    draft: str,
    out_path: str,
    model: str | None = None,
    max_iterations: int = 2,
    max_tokens: int = 2000,
    temperature: float = 0.2,
    include_log: bool = False,
) -> dict[str, Any]:
    """Take an original task + a draft answer; produce a corrected version.

    Returns the same metadata shape as `ask`.

    Note: `max_results` is accepted for backward compatibility but is no longer
    used — web_search uses its built-in default (5).
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
        "answer_preview": result["answer"][:200],
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


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False
))
async def verify_project(
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


# =============================================================================
# Mechanical-edit guardrail
# =============================================================================

# Tasks shorter than this are very likely mechanical and not worth a model
# round-trip. Tunable via env.
_GUARDRAIL_TASK_CHAR_LIMIT = int(os.environ.get("VLLM_AGENT_GUARDRAIL_CHAR_LIMIT", "240"))

_MECHANICAL_TASK_HINTS = (
    "rename ", "replace ", "find and replace", "find-and-replace",
    "change every ", "swap ", "sed ", "s/", "substitute ", "global replace",
    "typo", "rename the ", "rename all ",
)


def _looks_mechanical(task: str) -> bool:
    """Heuristic: is `task` something the orchestrator should do inline rather
    than pay a dispatch round-trip for? True if the task is short AND mentions
    a mechanical-edit keyword."""
    t = task.strip().lower()
    if len(t) > _GUARDRAIL_TASK_CHAR_LIMIT:
        return False
    return any(h in t for h in _MECHANICAL_TASK_HINTS)


# =============================================================================
# Tools: agent dispatch
# =============================================================================

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True
))
async def agent_run(
    task: str,
    skill: str | None = None,
    mode: str = "remote",
    workdir: str | None = None,
    out_dir: str | None = None,
    model: str | None = None,
    max_iterations: int = 12,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    timeout_s: int = 1800,
    extra_context: list[str] | None = None,
) -> dict[str, Any]:
    """Dispatch a coding-agent task to the vllm-rtx5090 backend.

    `mode='local'` runs the agent loop in this process (worker tools execute on
    the user's machine; bash requires VLLM_AGENT_LOCAL_BASH=1).
    `mode='remote'` POSTs to the VM-side vllm-agent serve (worker tools execute
    in the VM; full Bash; requires VLLM_AGENT_URL to be set).

    When `skill` is provided the orchestrator resolves the skill markdown locally
    and ships the resolved content to the worker via `skill_content`, so the
    worker never needs filesystem access to the skill definitions.

    Returns metadata only: run_id, out_dir, summary_path, files_changed,
    diff_path, iterations, duration_s, status, error, search_log.
    The actual agent output is on disk under out_dir.

    Guardrail: tiny mechanical tasks (rename/replace/sed-style) are refused
    early. Use the `fast_edit` tool instead — it bypasses the model loop
    and applies a literal find/replace at near-zero cost. Set the env var
    VLLM_AGENT_GUARDRAIL_CHAR_LIMIT=0 to disable.
    """
    if _GUARDRAIL_TASK_CHAR_LIMIT > 0 and _looks_mechanical(task):
        return {
            "status": "refused",
            "reason": "mechanical_edit_guardrail",
            "error": (
                "Task looks like a mechanical find/replace and is too short "
                "to justify a model dispatch. Use `fast_edit(path, old, new)` "
                "to apply the change directly, or do the edit inline. "
                "Override by setting env VLLM_AGENT_GUARDRAIL_CHAR_LIMIT=0."
            ),
            "task_chars": len(task),
        }
    skill_content: str | None = None
    if skill:
        try:
            skill_content = _SkillLoader().load_skill(skill)
        except _SkillNotFound as exc:
            return {"status": "error", "error": str(exc)}
    req = _AgentRunRequest(
        task=task, skill=skill, skill_content=skill_content, mode=mode,
        workdir=workdir, out_dir=out_dir, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature, timeout_s=timeout_s, extra_context=extra_context,
        env_overlay=_build_env_overlay() or None,
    )
    if mode == "local":
        result = await _agent_run_local(req)
        return asdict(result)
    else:
        return await _agent_run_remote(req)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True
))
async def agent_session_start(
    goal: str,
    skill: str | None = None,
    mode: str = "remote",
    workdir: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Start a long-running agent session. Returns: {session_id, out_dir, status}.

    When `skill` is provided the orchestrator resolves the skill markdown locally
    and ships the resolved content to the worker via `skill_content`, so the
    worker never needs filesystem access to the skill definitions.
    """
    skill_content: str | None = None
    if skill:
        try:
            skill_content = _SkillLoader().load_skill(skill)
        except _SkillNotFound as exc:
            return {"status": "error", "error": str(exc)}
    if mode == "local":
        req = _AgentSessionStartRequest(goal=goal, skill=skill,
                                        skill_content=skill_content,
                                        mode=mode, workdir=workdir, model=model,
                                        env_overlay=_build_env_overlay() or None)
        return asdict(await _ass_local(req))
    return await _http_session_start({
        "goal": goal, "skill": skill, "skill_content": skill_content,
        "mode": "remote", "workdir": workdir, "model": model,
        "env_overlay": _build_env_overlay() or None,
    })


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True
))
async def agent_session_step(
    session_id: str,
    nudge: str | None = None,
    max_iterations: int = 10,
    mode: str = "remote",
) -> dict[str, Any]:
    """Run one step of a session. Returns step metadata including step_status."""
    if mode == "local":
        return asdict(await _aststep_local(session_id, nudge=nudge,
                                            max_iterations=max_iterations))
    return await _http_session_step(session_id, {
        "nudge": nudge, "max_iterations": max_iterations,
    })


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True
))
async def agent_session_status(session_id: str, mode: str = "remote") -> dict[str, Any]:
    """Get the current state of a session."""
    if mode == "local":
        return asdict(await _aststatus_local(session_id))
    return await _http_session_status(session_id)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True
))
async def agent_session_stop(session_id: str, mode: str = "remote") -> dict[str, Any]:
    """Stop a session. Subsequent steps return immediately with status=stopped."""
    if mode == "local":
        return asdict(await _aststop_local(session_id))
    return await _http_session_stop(session_id)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
))
async def fast_edit(
    path: str,
    old: str,
    new: str,
    replace_all: bool = False,
    mode: str = "local",
    workdir: str | None = None,
) -> dict[str, Any]:
    """Apply a literal find/replace on a single file — no model call.

    Use this for mechanical changes (renames, typo fixes, single-line tweaks)
    where dispatching `agent_run` would pay the boot + system-prompt cost on
    work the orchestrator could do directly. `old` must appear in the file;
    by default it must be unique (set replace_all=true to replace every
    occurrence).

    mode='local'  applies the edit on this machine.
    mode='remote' POSTs to the VM-side vllm-agent /fast_edit endpoint.

    Returns: {"path", "replacements", "bytes_written"}.
    """
    if mode == "local":
        from pathlib import Path as _P
        base = _P(workdir).expanduser().resolve() if workdir else _P.cwd()
        candidate = (base / path).resolve() if not _P(path).is_absolute() else _P(path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return {"status": "error",
                    "error": f"path {path!r} escapes workdir {base}"}
        try:
            text = candidate.read_text()
        except FileNotFoundError:
            return {"status": "error", "error": f"file not found: {candidate}"}
        if old not in text:
            return {"status": "error", "error": f"old string not found in {candidate}"}
        occurrences = text.count(old)
        if not replace_all and occurrences > 1:
            return {
                "status": "error",
                "error": (f"old string is not unique ({occurrences} occurrences); "
                          "set replace_all=true or provide more context"),
            }
        new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        candidate.write_text(new_text)
        return {
            "path": str(candidate),
            "replacements": occurrences if replace_all else 1,
            "bytes_written": len(new_text.encode()),
        }
    # mode == "remote"
    return await _http_agent("POST", "/fast_edit", body={
        "path": path, "old": old, "new": new,
        "replace_all": replace_all, "workdir": workdir,
    }, timeout=15.0)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True
))
async def agent_run_artifacts(
    out_dir: str,
    mode: str = "remote",
    tail_lines: int = 50,
) -> dict[str, Any]:
    """Read back the artifacts (summary.md, files_changed.txt, transcript tail)
    of a completed agent run.

    For mode='local' the artifacts live on the user's machine; this tool reads
    them directly. For mode='remote' the artifacts live on the VM; this tool
    fetches them via the vllm-agent HTTP API.

    Returns: {out_dir, summary, files_changed, transcript_tail}.
    """
    if mode == "local":
        from pathlib import Path
        base = Path(out_dir).expanduser()
        if not base.is_dir():
            return {"error": f"out_dir not found: {base}"}
        summary = ""
        summary_p = base / "summary.md"
        if summary_p.exists():
            summary = summary_p.read_text()
        files_changed: list[str] = []
        fc_p = base / "files_changed.txt"
        if fc_p.exists():
            files_changed = [ln for ln in fc_p.read_text().splitlines() if ln.strip()]
        transcript_tail: list[dict] = []
        t_p = base / "transcript.jsonl"
        if t_p.exists():
            lines = [ln for ln in t_p.read_text().splitlines() if ln.strip()]
            for ln in lines[-tail_lines:]:
                try:
                    transcript_tail.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        return {
            "out_dir": str(base),
            "summary": summary,
            "files_changed": files_changed,
            "transcript_tail": transcript_tail,
        }
    # mode == "remote"
    return await _http_agent(
        "GET", "/artifacts",
        params={"out_dir": out_dir, "tail_lines": tail_lines},
        timeout=15.0,
    )


# =============================================================================
# Tools: skill discovery
# =============================================================================

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False
))
async def list_skills() -> list[dict[str, Any]]:
    """List all skills discoverable from configured roots (project, user,
    superpowers). Returns: [{"name", "source", "path", "description"}, ...].
    Skill names are passed to `agent_run(skill=...)` and `agent_session_start(skill=...)`.
    """
    return _SkillLoader().list_skills()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
