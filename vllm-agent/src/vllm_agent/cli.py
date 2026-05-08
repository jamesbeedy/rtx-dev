"""Typer CLI: `vllm-agent run`, `serve`, `list-skills`."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from .api import AgentRunRequest, agent_run
from .skills import SkillLoader

app = typer.Typer(help="Agent runtime backed by vLLM.")


@app.command("run")
def cmd_run(
    task: str = typer.Argument(..., help="The task instruction for the worker."),
    skill: str | None = typer.Option(None, help="Skill name, e.g. superpowers:tdd."),
    mode: str = typer.Option("remote", help="local | remote"),
    workdir: str | None = typer.Option(None, help="Working directory."),
    out_dir: str | None = typer.Option(None, help="Output dir for transcript/summary."),
    max_iterations: int = typer.Option(30),
    max_tokens: int = typer.Option(4096),
    temperature: float = typer.Option(0.2),
) -> None:
    """Run a one-shot agent task and print the result as JSON."""
    req = AgentRunRequest(
        task=task, skill=skill, mode=mode, workdir=workdir, out_dir=out_dir,
        max_iterations=max_iterations, max_tokens=max_tokens, temperature=temperature,
    )
    result = asyncio.run(agent_run(req))
    typer.echo(json.dumps(result.__dict__, indent=2, default=str))


@app.command("list-skills")
def cmd_list_skills() -> None:
    """List all skills discoverable from the configured roots."""
    for s in SkillLoader().list_skills():
        typer.echo(f"{s['name']}\t{s['description']}")


@app.command("serve")
def cmd_serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8088),
) -> None:
    """Start the FastAPI HTTP server (used by mode=remote)."""
    import uvicorn
    from .server import app as fastapi_app
    uvicorn.run(fastapi_app, host=host, port=port)


if __name__ == "__main__":
    app()
