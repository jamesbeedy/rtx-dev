"""Public API: agent_run + agent_session_*. CLI and HTTP server call these."""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loop import LoopConfig, run_loop
from .prompts import build_system_prompt, build_user_prompt
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


async def agent_run(req: AgentRunRequest) -> AgentRunResult:
    run_id = uuid.uuid4().hex[:12]
    out_dir = Path(req.out_dir) if req.out_dir else (DEFAULT_RUN_ROOT / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    ws = Workspace.resolve(req.workdir)
    transcript = Transcript(out_dir / "transcript.jsonl")
    skill_content = SkillLoader().load_skill(req.skill) if req.skill else None
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
    t0 = time.perf_counter()
    loop_result = await run_loop(msgs, ctx, cfg)
    duration = time.perf_counter() - t0

    files_changed = _files_changed(before, ws.root)
    (out_dir / "files_changed.txt").write_text("\n".join(files_changed) + ("\n" if files_changed else ""))

    summary_path = out_dir / "summary.md"
    if not summary_path.exists():
        # Worker didn't call finish() — synthesize a placeholder.
        body = loop_result.final_message_content or "(no final summary; worker did not call finish())"
        summary_path.write_text(body)

    return AgentRunResult(
        run_id=run_id,
        out_dir=str(out_dir),
        summary_path=str(summary_path),
        files_changed=files_changed,
        diff_path=None,   # filled in Plan B (git diff after run, when in remote mode)
        iterations=loop_result.iterations,
        duration_s=round(duration, 2),
        status=loop_result.status,
        error=loop_result.error,
    )
