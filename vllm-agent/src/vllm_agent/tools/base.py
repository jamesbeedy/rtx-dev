"""Base contract for worker tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

ToolFn = Callable[[dict[str, Any], "ToolContext"], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    schema: dict[str, Any]   # OpenAI function-calling JSON schema
    execute: ToolFn


@dataclass
class ToolContext:
    """Runtime context passed to every tool call."""
    workspace: Any           # vllm_agent.workspace.Workspace
    transcript: Any          # vllm_agent.transcript.Transcript
    env: dict[str, str]      # subset of os.environ snapshotted at run start
