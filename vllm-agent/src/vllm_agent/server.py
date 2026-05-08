"""FastAPI app exposing agent_run and agent_session_* over HTTP."""
from __future__ import annotations

import os
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .api import (
    AgentRunRequest, agent_run,
    AgentSessionStartRequest, agent_session_start,
    agent_session_step, agent_session_status, agent_session_stop,
)
from .skills import SkillLoader

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


@app.post("/run")
async def run(body: RunBody) -> dict:
    result = await agent_run(AgentRunRequest(**body.model_dump()))
    return asdict(result)


@app.post("/session")
async def session_start(body: SessionStartBody) -> dict:
    result = await agent_session_start(AgentSessionStartRequest(**body.model_dump()))
    return asdict(result)


@app.post("/session/{session_id}/step")
async def session_step(session_id: str, body: SessionStepBody) -> dict:
    try:
        result = await agent_session_step(
            session_id, nudge=body.nudge, max_iterations=body.max_iterations)
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")
    return asdict(result)


@app.get("/session/{session_id}")
async def session_status(session_id: str) -> dict:
    try:
        return asdict(await agent_session_status(session_id))
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")


@app.post("/session/{session_id}/stop")
async def session_stop(session_id: str) -> dict:
    try:
        result = await agent_session_stop(session_id)
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")
    return asdict(result)


@app.get("/skills")
async def skills() -> list[dict]:
    return SkillLoader().list_skills()
