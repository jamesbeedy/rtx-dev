"""FastAPI app exposing agent_run and agent_session_* over HTTP."""
from __future__ import annotations

import os
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .api import (
    AgentRunRequest, agent_run,
    AgentSessionStartRequest, agent_session_start,
    agent_session_step, agent_session_status, agent_session_stop,
)
from .skills import SkillLoader

VLLM_AGENT_API_KEY = os.environ.get("VLLM_AGENT_API_KEY", "")


async def require_key(authorization: str | None = Header(None)) -> None:
    """When VLLM_AGENT_API_KEY is set, require `Authorization: Bearer <key>`."""
    if not VLLM_AGENT_API_KEY:
        return
    expected = f"Bearer {VLLM_AGENT_API_KEY}"
    if authorization != expected:
        raise HTTPException(401, "invalid or missing API key")


app = FastAPI(title="vllm-agent")


class RunBody(BaseModel):
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


class SessionStartBody(BaseModel):
    goal: str
    skill: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None


class SessionStepBody(BaseModel):
    nudge: str | None = None
    max_iterations: int = 10


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "vllm_base_url": os.environ.get("VLLM_BASE_URL"),
        "vllm_model": os.environ.get("VLLM_MODEL"),
    }


@app.post("/run", dependencies=[Depends(require_key)])
async def run(body: RunBody) -> dict:
    result = await agent_run(AgentRunRequest(**body.model_dump()))
    return asdict(result)


@app.post("/session", dependencies=[Depends(require_key)])
async def session_start(body: SessionStartBody) -> dict:
    result = await agent_session_start(AgentSessionStartRequest(**body.model_dump()))
    return asdict(result)


@app.post("/session/{session_id}/step", dependencies=[Depends(require_key)])
async def session_step(session_id: str, body: SessionStepBody) -> dict:
    try:
        result = await agent_session_step(
            session_id, nudge=body.nudge, max_iterations=body.max_iterations)
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")
    return asdict(result)


@app.get("/session/{session_id}", dependencies=[Depends(require_key)])
async def session_status(session_id: str) -> dict:
    try:
        return asdict(await agent_session_status(session_id))
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")


@app.post("/session/{session_id}/stop", dependencies=[Depends(require_key)])
async def session_stop(session_id: str) -> dict:
    try:
        result = await agent_session_stop(session_id)
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")
    return asdict(result)


@app.get("/skills", dependencies=[Depends(require_key)])
async def skills() -> list[dict]:
    return SkillLoader().list_skills()


@app.get("/artifacts", dependencies=[Depends(require_key)])
async def artifacts(out_dir: str, tail_lines: int = 50) -> dict:
    """Read back the standard artifacts of a completed run.

    Returns: {out_dir, summary, files_changed, transcript_tail}.
    `transcript_tail` is the last `tail_lines` parsed JSONL records.
    """
    import json
    from pathlib import Path

    base = Path(out_dir).expanduser()
    if not base.is_dir():
        raise HTTPException(404, f"out_dir not found: {base}")

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
