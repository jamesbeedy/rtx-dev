"""Public API: agent_run + agent_session_*. CLI and HTTP server call these."""
from __future__ import annotations

import os
import time
import uuid
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loop import LoopConfig, run_loop
from .prompts import build_system_prompt, build_user_prompt
from .sessions import SessionStore, SessionStatus
from .skills import SkillLoader
from .tools import ToolContext, WORKER_TOOLS  # noqa: F401  (force tool registration)
from .tools import fs as _fs                  # noqa: F401
from .tools import shell as _shell            # noqa: F401
from .tools import search as _search          # noqa: F401
from .tools import finish as _finish          # noqa: F401
from .transcript import Transcript
from .workspace import Workspace


# Default location for run outputs when caller doesn't specify out_dir.
DEFAULT_RUN_ROOT = Path("~/.cache/vllm-agent/runs").expanduser()


@dataclass
class AgentRunRequest:
    task: str
    skill: str | None = None
    skill_content: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    out_dir: str | None = None
    model: str | None = None
    max_iterations: int = 30
    max_tokens: int = 4096
    temperature: float = 0.2
    timeout_s: int = 1800
    extra_context: list[str] | None = None


@dataclass
class AgentRunResult:
    run_id: str
    out_dir: str
    summary_path: str
    files_changed: list[str]
    diff_path: str | None
    iterations: int
    duration_s: float
    status: str
    error: str | None = None
    search_log: list[dict] = field(default_factory=list)


def _snapshot_files(workdir: Path) -> dict[str, float]:
    """Return path → mtime for all files in workdir (used to detect changes)."""
    out: dict[str, float] = {}
    for p in workdir.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    return out


def _files_changed(before: dict[str, float], workdir: Path) -> list[str]:
    after = _snapshot_files(workdir)
    changed: set[str] = set()
    for path, mt in after.items():
        if path not in before or before[path] != mt:
            changed.add(path)
    for path in before:
        if path not in after:
            changed.add(path)
    return sorted(str(Path(p).relative_to(workdir)) for p in changed)


def _capture_diff(workdir: Path, out_dir: Path) -> str | None:
    """If workdir is a git repo, write `git diff` to diff.patch and return its path."""
    if not (workdir / ".git").is_dir():
        return None
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "diff", "--no-color"],
            cwd=str(workdir),
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    diff_path = out_dir / "diff.patch"
    diff_path.write_text(proc.stdout)
    return str(diff_path)


def _extract_search_log(transcript_path: Path) -> list[dict]:
    """Pull web_search records out of the transcript JSONL."""
    if not transcript_path.exists():
        return []
    out: list[dict] = []
    for line in transcript_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "tool_call" and rec.get("tool") == "web_search":
            args = rec.get("args") or {}
            result = rec.get("result") or {}
            out.append({
                "query": args.get("query", ""),
                "n_results": len(result.get("results", []) or []),
                "error": result.get("error"),
            })
    return out


async def agent_run(req: AgentRunRequest) -> AgentRunResult:
    run_id = uuid.uuid4().hex[:12]
    out_dir = Path(req.out_dir) if req.out_dir else (DEFAULT_RUN_ROOT / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    ws = Workspace.resolve(req.workdir)
    transcript = Transcript(out_dir / "transcript.jsonl")
    if req.skill_content:
        skill_content = req.skill_content
    elif req.skill:
        skill_content = SkillLoader().load_skill(req.skill)
    else:
        skill_content = None
    system_prompt = build_system_prompt(skill_content, str(ws.root), req.mode)
    user_prompt = build_user_prompt(req.task, req.extra_context)

    ctx = ToolContext(
        workspace=ws,
        transcript=transcript,
        env={
            "VLLM_AGENT_MODE": req.mode,
            "VLLM_AGENT_LOCAL_BASH": os.environ.get("VLLM_AGENT_LOCAL_BASH", ""),
            "VLLM_AGENT_OUT_DIR": str(out_dir),
        },
    )
    cfg = LoopConfig(
        vllm_base_url=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000"),
        vllm_model=req.model or os.environ.get("VLLM_MODEL", ""),
        max_iterations=req.max_iterations,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        api_key=os.environ.get("VLLM_API_KEY") or None,
        request_timeout_s=float(req.timeout_s),
    )

    before = _snapshot_files(ws.root)
    transcript.record_message("system", system_prompt)
    transcript.record_message("user", user_prompt)
    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    import asyncio
    t0 = time.perf_counter()
    try:
        loop_result = await asyncio.wait_for(run_loop(msgs, ctx, cfg),
                                             timeout=float(req.timeout_s))
    except asyncio.TimeoutError:
        duration = time.perf_counter() - t0
        files_changed = _files_changed(before, ws.root)
        (out_dir / "files_changed.txt").write_text(
            "\n".join(files_changed) + ("\n" if files_changed else ""))
        summary_path = out_dir / "summary.md"
        if not summary_path.exists():
            summary_path.write_text("(timed out before finish)")
        diff_path = _capture_diff(ws.root, out_dir)
        return AgentRunResult(
            run_id=run_id, out_dir=str(out_dir), summary_path=str(summary_path),
            files_changed=files_changed, diff_path=diff_path,
            iterations=0, duration_s=round(duration, 2),
            status="timeout", error=f"agent_run exceeded timeout_s={req.timeout_s}",
            search_log=_extract_search_log(out_dir / "transcript.jsonl"),
        )
    duration = time.perf_counter() - t0

    files_changed = _files_changed(before, ws.root)
    (out_dir / "files_changed.txt").write_text("\n".join(files_changed) + ("\n" if files_changed else ""))

    summary_path = out_dir / "summary.md"
    if not summary_path.exists():
        # Worker didn't call finish() — synthesize a placeholder.
        body = loop_result.final_message_content or "(no final summary; worker did not call finish())"
        summary_path.write_text(body)

    diff_path = _capture_diff(ws.root, out_dir)
    return AgentRunResult(
        run_id=run_id,
        out_dir=str(out_dir),
        summary_path=str(summary_path),
        files_changed=files_changed,
        diff_path=diff_path,
        iterations=loop_result.iterations,
        duration_s=round(duration, 2),
        status=loop_result.status,
        error=loop_result.error,
        search_log=_extract_search_log(out_dir / "transcript.jsonl"),
    )


# ---- session API ------------------------------------------------------------

DEFAULT_SESSION_ROOT = Path("~/.cache/vllm-agent/sessions").expanduser()


def _session_store() -> SessionStore:
    root = Path(os.environ.get("VLLM_AGENT_SESSION_ROOT", str(DEFAULT_SESSION_ROOT)))
    return SessionStore(root=root)


@dataclass
class AgentSessionStartRequest:
    goal: str
    skill: str | None = None
    skill_content: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None


@dataclass
class AgentSessionStartResult:
    session_id: str
    out_dir: str
    status: str


async def agent_session_start(req: AgentSessionStartRequest) -> AgentSessionStartResult:
    ws = Workspace.resolve(req.workdir)
    store = _session_store()
    s = store.create(
        goal=req.goal,
        skill=req.skill,
        skill_content=req.skill_content,
        mode=req.mode,
        workdir=str(ws.root),
        model=req.model,
    )
    return AgentSessionStartResult(
        session_id=s.session_id,
        out_dir=str(store.session_dir(s.session_id)),
        status=s.status.value,
    )


@dataclass
class AgentSessionStepResult:
    session_id: str
    iterations_this_step: int
    files_changed_this_step: list[str]
    summary_path: str
    status: str
    step_status: str


async def agent_session_step(
    session_id: str,
    nudge: str | None = None,
    max_iterations: int = 10,
) -> AgentSessionStepResult:
    store = _session_store()
    s = store.load(session_id)
    if s.status in (SessionStatus.STOPPED, SessionStatus.COMPLETED, SessionStatus.ERRORED):
        return AgentSessionStepResult(
            session_id=session_id, iterations_this_step=0,
            files_changed_this_step=[],
            summary_path=str(store.session_dir(session_id) / "summary.md"),
            status=s.status.value,
            step_status="not_started",
        )

    ws = Workspace.resolve(s.workdir)
    sess_dir = store.session_dir(session_id)
    transcript = Transcript(sess_dir / "transcript.jsonl")

    if s.skill_content:
        skill_content = s.skill_content
    elif s.skill:
        skill_content = SkillLoader().load_skill(s.skill)
    else:
        skill_content = None
    system_prompt = build_system_prompt(skill_content, str(ws.root), s.mode)
    user_prompt = build_user_prompt(s.goal, None)

    msgs = store.load_messages(session_id)
    if not msgs:
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for m in msgs:
            store.append_message(session_id, m)
    if nudge:
        msgs.append({"role": "user", "content": nudge})
        store.append_message(session_id, {"role": "user", "content": nudge})

    ctx = ToolContext(
        workspace=ws,
        transcript=transcript,
        env={
            "VLLM_AGENT_MODE": s.mode,
            "VLLM_AGENT_LOCAL_BASH": os.environ.get("VLLM_AGENT_LOCAL_BASH", ""),
            "VLLM_AGENT_OUT_DIR": str(sess_dir),
        },
    )
    cfg = LoopConfig(
        vllm_base_url=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000"),
        vllm_model=s.model or os.environ.get("VLLM_MODEL", ""),
        max_iterations=max_iterations,
        max_tokens=4096,
        temperature=0.2,
        api_key=os.environ.get("VLLM_API_KEY") or None,
    )

    before = _snapshot_files(ws.root)
    loop_result = await run_loop(msgs, ctx, cfg)
    files_changed = _files_changed(before, ws.root)

    # Persist new messages produced this step.
    new_msgs = loop_result.messages[len(msgs):]
    for m in new_msgs:
        store.append_message(session_id, m)
    store.add_files_changed(session_id, files_changed)
    store.bump_iterations(session_id, loop_result.iterations)
    if loop_result.status == "ok":
        store.set_status(session_id, SessionStatus.COMPLETED)
    elif loop_result.status == "error":
        store.set_status(session_id, SessionStatus.ERRORED)
    # else (max_iterations): leave as RUNNING for resumption.

    return AgentSessionStepResult(
        session_id=session_id,
        iterations_this_step=loop_result.iterations,
        files_changed_this_step=files_changed,
        summary_path=str(sess_dir / "summary.md"),
        status=store.load(session_id).status.value,
        step_status=loop_result.status,
    )


@dataclass
class AgentSessionStatusResult:
    session_id: str
    status: str
    iterations_total: int
    files_changed_total: list[str]
    started_at: float
    last_activity_at: float
    out_dir: str


async def agent_session_status(session_id: str) -> AgentSessionStatusResult:
    store = _session_store()
    s = store.load(session_id)
    return AgentSessionStatusResult(
        session_id=session_id,
        status=s.status.value,
        iterations_total=s.iterations_total,
        files_changed_total=s.files_changed_total,
        started_at=s.started_at,
        last_activity_at=s.last_activity_at,
        out_dir=str(store.session_dir(session_id)),
    )


@dataclass
class AgentSessionStopResult:
    session_id: str
    status: str


async def agent_session_stop(session_id: str) -> AgentSessionStopResult:
    store = _session_store()
    store.set_status(session_id, SessionStatus.STOPPED)
    return AgentSessionStopResult(session_id=session_id, status="stopped")
