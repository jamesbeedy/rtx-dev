# Worker git tools + GitHub PAT pass-through — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the remote `vllm-agent` worker run `git`/`gh` commands authenticated by the user's PAT, with the token, name, and email flowing per-request from `.mcp.json` through the MCP server to the worker subprocess env.

**Architecture:** Add an `env_overlay: dict[str,str]` field that flows MCP server → `/run` POST body → `AgentRunRequest` → `ToolContext` → `bash` subprocess env. The MCP server applies an allowlist (`GITHUB_TOKEN`, `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`) when reading from its own env. The `Transcript` writer redacts any overlay value substrings before writing JSONL. Container Dockerfile installs `git` and `gh`.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, asyncio, pytest (anyio mode), Docker Compose, Debian apt.

---

## Spec reference

`docs/superpowers/specs/2026-05-11-worker-git-tools-design.md`

## File map

- Modify: `vllm-agent/src/vllm_agent/transcript.py` — add value-redaction list
- Modify: `vllm-agent/tests/test_transcript.py` — add redaction tests
- Modify: `vllm-agent/src/vllm_agent/tools/base.py` — add `ToolContext.env_overlay`
- Modify: `vllm-agent/src/vllm_agent/tools/shell.py` — merge overlay into subprocess env
- Modify: `vllm-agent/tests/test_tools_shell.py` — add overlay-env tests
- Modify: `vllm-agent/src/vllm_agent/api.py` — `env_overlay` on `AgentRunRequest`/`AgentSessionStartRequest`, plumb into `ToolContext` and `Transcript`
- Modify: `vllm-agent/src/vllm_agent/sessions.py` — persist `env_overlay` with session
- Modify: `vllm-agent/tests/test_api.py` — add overlay end-to-end tests
- Modify: `vllm-agent/src/vllm_agent/server.py` — `RunBody`/`SessionStartBody` accept `env_overlay`
- Modify: `vllm-agent/tests/test_server.py` — POST body with `env_overlay`
- Modify: `mcp-server/vllm_mcp.py` — read three allowlisted env vars, forward in remote/local dispatch
- Modify: `compose/vllm-agent/Dockerfile` — install `git` + `gh`
- Modify: `mcp-server/example-mcp-config.json` — example keys
- Modify: `README.md` — short note on PAT config

---

## Task 1: Transcript value redaction

**Files:**
- Modify: `vllm-agent/src/vllm_agent/transcript.py`
- Test: `vllm-agent/tests/test_transcript.py`

- [ ] **Step 1: Write failing tests**

Add to `vllm-agent/tests/test_transcript.py`:

```python
def test_transcript_redacts_overlay_values(tmp_path):
    t = Transcript(tmp_path / "t.jsonl", redact_values=["ghp_supersecrettoken"])
    t.append({"kind": "tool_call", "tool": "bash",
              "args": {"command": "echo ghp_supersecrettoken"},
              "result": {"stdout": "ghp_supersecrettoken\n"}})
    text = (tmp_path / "t.jsonl").read_text()
    assert "ghp_supersecrettoken" not in text
    assert "[REDACTED]" in text


def test_transcript_skips_short_redact_values(tmp_path):
    # Short values (<8 chars) are skipped to avoid mangling unrelated text.
    t = Transcript(tmp_path / "t.jsonl", redact_values=["abc"])
    t.append({"role": "user", "content": "abcdef"})
    text = (tmp_path / "t.jsonl").read_text()
    assert "abcdef" in text  # unchanged


def test_transcript_no_redact_values_default(tmp_path):
    t = Transcript(tmp_path / "t.jsonl")
    t.append({"role": "user", "content": "hello"})
    assert "hello" in (tmp_path / "t.jsonl").read_text()
```

- [ ] **Step 2: Run tests to verify failures**

Run: `cd vllm-agent && python -m pytest tests/test_transcript.py -v`
Expected: 2 new tests FAIL (`redact_values` argument not accepted); existing tests PASS.

- [ ] **Step 3: Implement redaction**

Replace `vllm-agent/src/vllm_agent/transcript.py` contents:

```python
"""Transcript: append-only JSONL recorder for an agent run."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MIN_REDACT_LEN = 8


@dataclass
class Transcript:
    path: Path
    redact_values: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        # Only redact values long enough to be unique-ish (avoids mangling
        # unrelated short substrings).
        self._redact = [v for v in self.redact_values if v and len(v) >= _MIN_REDACT_LEN]

    def _scrub(self, line: str) -> str:
        for v in self._redact:
            if v in line:
                line = line.replace(v, "[REDACTED]")
        return line

    def append(self, record: dict[str, Any]) -> None:
        record = {"ts": time.time(), **record}
        line = json.dumps(record, ensure_ascii=False)
        with self.path.open("a") as f:
            f.write(self._scrub(line) + "\n")

    def record_message(self, role: str, content: Any) -> None:
        self.append({"kind": "message", "role": role, "content": content})

    def record_tool_call(self, tool: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        self.append({"kind": "tool_call", "tool": tool, "args": args, "result": result})
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd vllm-agent && python -m pytest tests/test_transcript.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/transcript.py vllm-agent/tests/test_transcript.py
git commit -m "feat(transcript): redact configured values before write"
```

---

## Task 2: `ToolContext.env_overlay` field

**Files:**
- Modify: `vllm-agent/src/vllm_agent/tools/base.py`

- [ ] **Step 1: Update dataclass**

Replace `vllm-agent/src/vllm_agent/tools/base.py` contents:

```python
"""Base contract for worker tools."""
from __future__ import annotations

from dataclasses import dataclass, field
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
    workspace: Any                                # vllm_agent.workspace.Workspace
    transcript: Any                               # vllm_agent.transcript.Transcript
    env: dict[str, str]                           # subset of os.environ snapshotted at run start
    env_overlay: dict[str, str] = field(default_factory=dict)  # keys merged into bash subprocess env
```

- [ ] **Step 2: Run existing test suite to confirm no regressions**

Run: `cd vllm-agent && python -m pytest tests/test_tools_shell.py tests/test_tools_fs.py tests/test_tools_registry.py -v`
Expected: all tests PASS (existing fixtures use positional/keyword args that still match).

- [ ] **Step 3: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/base.py
git commit -m "feat(tools): add ToolContext.env_overlay field"
```

---

## Task 3: `bash` tool merges overlay into subprocess env

**Files:**
- Modify: `vllm-agent/src/vllm_agent/tools/shell.py`
- Test: `vllm-agent/tests/test_tools_shell.py`

- [ ] **Step 1: Write failing tests**

Append to `vllm-agent/tests/test_tools_shell.py`:

```python
async def test_bash_overlay_env_visible(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_MODE": "remote"},
        env_overlay={"GITHUB_TOKEN": "ghp_test_value_1234"},
    )
    out = await bash_tool.execute(
        {"command": "echo token=$GITHUB_TOKEN"}, ctx)
    assert out["exit_code"] == 0
    assert "token=ghp_test_value_1234" in out["stdout"]


async def test_bash_overlay_overrides_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_OVERRIDE_VAR", "from_parent")
    ws = Workspace.resolve(str(tmp_path))
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_MODE": "remote"},
        env_overlay={"MY_OVERRIDE_VAR": "from_overlay"},
    )
    out = await bash_tool.execute(
        {"command": "echo $MY_OVERRIDE_VAR"}, ctx)
    assert "from_overlay" in out["stdout"]
    assert "from_parent" not in out["stdout"]
```

- [ ] **Step 2: Run tests to verify failures**

Run: `cd vllm-agent && python -m pytest tests/test_tools_shell.py::test_bash_overlay_env_visible tests/test_tools_shell.py::test_bash_overlay_overrides_existing_env -v`
Expected: FAIL — overlay not applied; env var is empty or inherits parent only.

- [ ] **Step 3: Implement overlay pass-through**

Replace the `_bash` function body in `vllm-agent/src/vllm_agent/tools/shell.py`:

```python
async def _bash(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    import os
    command = args.get("command", "")
    cwd = args.get("cwd") or str(ctx.workspace.root)
    timeout_s = float(args.get("timeout_s", 60))

    # Safety gate: in local mode require explicit opt-in.
    if ctx.env.get("VLLM_AGENT_MODE") == "local" and ctx.env.get("VLLM_AGENT_LOCAL_BASH") != "1":
        return {"error": "Local-mode bash is disabled. Set VLLM_AGENT_LOCAL_BASH=1 "
                         "in the agent environment to enable it."}

    # Inherit parent env, then apply per-run overlay (e.g. GITHUB_TOKEN).
    subproc_env = {**os.environ, **ctx.env_overlay}

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=subproc_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"error": f"timeout after {timeout_s}s", "exit_code": -1,
                "stdout": "", "stderr": ""}
    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace")[:64_000],
        "stderr": stderr.decode(errors="replace")[:16_000],
    }
```

- [ ] **Step 4: Run tests**

Run: `cd vllm-agent && python -m pytest tests/test_tools_shell.py -v`
Expected: all tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/shell.py vllm-agent/tests/test_tools_shell.py
git commit -m "feat(bash): merge ToolContext.env_overlay into subprocess env"
```

---

## Task 4: `AgentRunRequest.env_overlay` + plumbing in `agent_run`

**Files:**
- Modify: `vllm-agent/src/vllm_agent/api.py`
- Test: `vllm-agent/tests/test_api.py`

- [ ] **Step 1: Write failing test**

Append to `vllm-agent/tests/test_api.py`:

```python
async def test_agent_run_passes_env_overlay_to_bash(tmp_path, monkeypatch):
    """env_overlay on AgentRunRequest reaches the bash subprocess and is redacted in transcript."""
    from vllm_agent.api import AgentRunRequest, agent_run

    # Force a deterministic single bash call by stubbing run_loop.
    captured: dict = {}

    async def fake_run_loop(msgs, ctx, cfg):
        from vllm_agent.tools.shell import bash_tool
        result = await bash_tool.execute(
            {"command": "echo TOKEN_IS=$GITHUB_TOKEN"}, ctx)
        captured["bash_result"] = result
        captured["ctx_overlay"] = ctx.env_overlay
        from vllm_agent.loop import LoopResult
        return LoopResult(
            messages=msgs, iterations=1, status="ok",
            final_message_content="done", error=None,
        )

    monkeypatch.setattr("vllm_agent.api.run_loop", fake_run_loop)

    req = AgentRunRequest(
        task="noop",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        env_overlay={"GITHUB_TOKEN": "ghp_overlaytest_98765"},
        timeout_s=30,
    )
    result = await agent_run(req)
    assert captured["ctx_overlay"] == {"GITHUB_TOKEN": "ghp_overlaytest_98765"}
    assert "TOKEN_IS=ghp_overlaytest_98765" in captured["bash_result"]["stdout"]
    # Transcript should have redacted the token (bash tool's args weren't
    # recorded by the stub, but the recorded fields shouldn't contain the value).
    transcript_text = (tmp_path / "out" / "transcript.jsonl").read_text()
    assert "ghp_overlaytest_98765" not in transcript_text
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd vllm-agent && python -m pytest tests/test_api.py::test_agent_run_passes_env_overlay_to_bash -v`
Expected: FAIL — `AgentRunRequest` has no `env_overlay` argument.

- [ ] **Step 3: Add field + plumbing**

In `vllm-agent/src/vllm_agent/api.py`:

Add `env_overlay` to the `AgentRunRequest` dataclass (just after `extra_context`):

```python
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
    env_overlay: dict[str, str] | None = None
```

In `agent_run`, replace the `transcript = Transcript(out_dir / "transcript.jsonl")` line with:

```python
    env_overlay = dict(req.env_overlay or {})
    transcript = Transcript(
        out_dir / "transcript.jsonl",
        redact_values=list(env_overlay.values()),
    )
```

And replace the `ctx = ToolContext(...)` block with:

```python
    ctx = ToolContext(
        workspace=ws,
        transcript=transcript,
        env={
            "VLLM_AGENT_MODE": req.mode,
            "VLLM_AGENT_LOCAL_BASH": os.environ.get("VLLM_AGENT_LOCAL_BASH", ""),
            "VLLM_AGENT_OUT_DIR": str(out_dir),
        },
        env_overlay=env_overlay,
    )
```

- [ ] **Step 4: Run test to verify pass**

Run: `cd vllm-agent && python -m pytest tests/test_api.py::test_agent_run_passes_env_overlay_to_bash -v`
Expected: PASS.

- [ ] **Step 5: Run full api test file to confirm no regressions**

Run: `cd vllm-agent && python -m pytest tests/test_api.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add vllm-agent/src/vllm_agent/api.py vllm-agent/tests/test_api.py
git commit -m "feat(api): env_overlay on AgentRunRequest plumbs to ToolContext + transcript redaction"
```

---

## Task 5: `env_overlay` on session start + persistence

**Files:**
- Modify: `vllm-agent/src/vllm_agent/api.py`
- Modify: `vllm-agent/src/vllm_agent/sessions.py`
- Test: `vllm-agent/tests/test_api.py`, `vllm-agent/tests/test_sessions.py`

- [ ] **Step 1: Read sessions store to find serialization point**

Run: `cd vllm-agent && grep -n "skill_content\|workdir\|mode\b" src/vllm_agent/sessions.py | head -40`
Expected: shows where `Session` dataclass fields live and where they are persisted.

- [ ] **Step 2: Add field to `Session` and `SessionStore.create`**

In `vllm-agent/src/vllm_agent/sessions.py`, find the `Session` dataclass and add:

```python
env_overlay: dict[str, str] = field(default_factory=dict)
```

Then update `SessionStore.create(...)` to accept and persist `env_overlay`:

```python
def create(
    self,
    *,
    goal: str,
    skill: str | None,
    skill_content: str | None,
    mode: str,
    workdir: str,
    model: str | None,
    env_overlay: dict[str, str] | None = None,
) -> Session:
    ...
    # ... existing body, but include `env_overlay=dict(env_overlay or {})` in the
    # Session(...) constructor call.
```

Ensure `Session` serialization (to/from `session.json` or equivalent) round-trips the new field. If `dataclasses.asdict` and `**data` are used, no other change is needed.

- [ ] **Step 3: Add field to `AgentSessionStartRequest` + use it**

In `vllm-agent/src/vllm_agent/api.py`:

```python
@dataclass
class AgentSessionStartRequest:
    goal: str
    skill: str | None = None
    skill_content: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None
    env_overlay: dict[str, str] | None = None
```

In `agent_session_start`, pass it through:

```python
    s = store.create(
        goal=req.goal,
        skill=req.skill,
        skill_content=req.skill_content,
        mode=req.mode,
        workdir=str(ws.root),
        model=req.model,
        env_overlay=req.env_overlay,
    )
```

In `agent_session_step`, build the transcript and context using the session's overlay:

```python
    transcript = Transcript(
        sess_dir / "transcript.jsonl",
        redact_values=list((s.env_overlay or {}).values()),
    )
    ...
    ctx = ToolContext(
        workspace=ws,
        transcript=transcript,
        env={
            "VLLM_AGENT_MODE": s.mode,
            "VLLM_AGENT_LOCAL_BASH": os.environ.get("VLLM_AGENT_LOCAL_BASH", ""),
            "VLLM_AGENT_OUT_DIR": str(sess_dir),
        },
        env_overlay=dict(s.env_overlay or {}),
    )
```

- [ ] **Step 4: Add test for session-start overlay round-trip**

Append to `vllm-agent/tests/test_api.py`:

```python
async def test_session_start_persists_env_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))
    from vllm_agent.api import (
        AgentSessionStartRequest, agent_session_start, _session_store,
    )
    req = AgentSessionStartRequest(
        goal="noop",
        workdir=str(tmp_path),
        env_overlay={"GITHUB_TOKEN": "ghp_sessoverlay_42"},
    )
    res = await agent_session_start(req)
    s = _session_store().load(res.session_id)
    assert s.env_overlay == {"GITHUB_TOKEN": "ghp_sessoverlay_42"}
```

- [ ] **Step 5: Run tests**

Run: `cd vllm-agent && python -m pytest tests/test_api.py tests/test_sessions.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add vllm-agent/src/vllm_agent/api.py vllm-agent/src/vllm_agent/sessions.py vllm-agent/tests/test_api.py
git commit -m "feat(api): env_overlay on AgentSessionStartRequest, persisted with session"
```

---

## Task 6: HTTP server bodies accept `env_overlay`

**Files:**
- Modify: `vllm-agent/src/vllm_agent/server.py`
- Test: `vllm-agent/tests/test_server.py`

- [ ] **Step 1: Write failing test**

Append to `vllm-agent/tests/test_server.py`:

```python
def test_run_body_accepts_env_overlay():
    from vllm_agent.server import RunBody
    body = RunBody(task="noop", env_overlay={"GITHUB_TOKEN": "ghp_x_1234567"})
    assert body.env_overlay == {"GITHUB_TOKEN": "ghp_x_1234567"}


def test_session_start_body_accepts_env_overlay():
    from vllm_agent.server import SessionStartBody
    body = SessionStartBody(goal="noop", env_overlay={"GITHUB_TOKEN": "ghp_y_1234567"})
    assert body.env_overlay == {"GITHUB_TOKEN": "ghp_y_1234567"}
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd vllm-agent && python -m pytest tests/test_server.py::test_run_body_accepts_env_overlay tests/test_server.py::test_session_start_body_accepts_env_overlay -v`
Expected: FAIL — Pydantic raises `validation error` for unknown field.

- [ ] **Step 3: Add field to bodies**

In `vllm-agent/src/vllm_agent/server.py`, update the two Pydantic models:

```python
class RunBody(BaseModel):
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
    env_overlay: dict[str, str] | None = None


class SessionStartBody(BaseModel):
    goal: str
    skill: str | None = None
    skill_content: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None
    env_overlay: dict[str, str] | None = None
```

`body.model_dump()` already propagates the new field into the request dataclasses; no other server change required.

- [ ] **Step 4: Run tests**

Run: `cd vllm-agent && python -m pytest tests/test_server.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/server.py vllm-agent/tests/test_server.py
git commit -m "feat(server): RunBody/SessionStartBody accept env_overlay"
```

---

## Task 7: MCP server reads allowlist + forwards in dispatch

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

- [ ] **Step 1: Add overlay builder at module top**

In `mcp-server/vllm_mcp.py`, add after the existing env-reading constants (`VLLM_AGENT_API_KEY = ...`):

```python
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
```

- [ ] **Step 2: Forward in `_agent_run_remote`**

Find `_agent_run_remote` (around line 181) and update the `body` dict:

```python
    body = {
        "task": req.task, "skill": req.skill, "mode": "remote",
        "workdir": req.workdir, "out_dir": req.out_dir, "model": req.model,
        "max_iterations": req.max_iterations, "max_tokens": req.max_tokens,
        "temperature": req.temperature, "timeout_s": req.timeout_s,
        "extra_context": req.extra_context, "skill_content": req.skill_content,
        "env_overlay": _build_env_overlay() or None,
    }
```

- [ ] **Step 3: Forward in `_http_session_start` callers (in `agent_session_start` tool)**

Find the `agent_session_start` MCP tool (around line 906) and update the remote-branch payload:

```python
    return await _http_session_start({
        "goal": goal, "skill": skill, "skill_content": skill_content,
        "mode": "remote", "workdir": workdir, "model": model,
        "env_overlay": _build_env_overlay() or None,
    })
```

- [ ] **Step 4: Forward in local-mode dispatch**

Update the local-mode branches of `agent_run` and `agent_session_start` tools so local-mode also benefits:

In `agent_run` MCP tool (around line 859), update the `_AgentRunRequest(...)` constructor:

```python
    req = _AgentRunRequest(
        task=task, skill=skill, skill_content=skill_content, mode=mode,
        workdir=workdir, out_dir=out_dir, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature, timeout_s=timeout_s, extra_context=extra_context,
        env_overlay=_build_env_overlay() or None,
    )
```

In `agent_session_start` MCP tool, update the local-branch:

```python
    if mode == "local":
        req = _AgentSessionStartRequest(
            goal=goal, skill=skill,
            skill_content=skill_content,
            mode=mode, workdir=workdir, model=model,
            env_overlay=_build_env_overlay() or None,
        )
        return asdict(await _ass_local(req))
```

- [ ] **Step 5: Smoke-check MCP server module loads**

Run: `cd mcp-server && python -c "import vllm_mcp; print(vllm_mcp._build_env_overlay())"`
Expected: prints `{}` if no env vars are set, or the dict of any set allowlist keys. No errors.

- [ ] **Step 6: Commit**

```bash
git add mcp-server/vllm_mcp.py
git commit -m "feat(mcp): forward GITHUB_TOKEN/GIT_AUTHOR_* to worker as env_overlay"
```

---

## Task 8: Install `git` and `gh` in the worker container

**Files:**
- Modify: `compose/vllm-agent/Dockerfile`

- [ ] **Step 1: Replace Dockerfile contents**

Replace `compose/vllm-agent/Dockerfile` contents:

```dockerfile
# Tiny vllm-agent runtime image: python:3.12-slim + an agent user with home dir.
# Source is bind-mounted from the host at /app; pip install -e runs at container
# start (compose's `command:`).
#
# Container starts as root so the entrypoint can chown the named-volume mount
# points (Docker mounts named volumes as root regardless of USER), then drops
# to the agent user via gosu before running the supplied CMD.

FROM python:3.12-slim

# Base packages plus git and the GitHub CLI. gh is installed from the official
# apt repo so the version is current and signed.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        gosu git ca-certificates curl gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 -s /bin/bash agent \
 && mkdir -p /home/agent/.cache/vllm-agent/runs \
              /home/agent/.cache/vllm-agent/workspaces \
              /var/lib/vllm-agent/sessions \
 && chown -R agent:agent /home/agent /var/lib/vllm-agent

WORKDIR /app

ENV HOME=/home/agent \
    PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

- [ ] **Step 2: Build locally to confirm Dockerfile is valid**

Run: `docker build -t vllm-agent-test compose/vllm-agent/`
Expected: build succeeds. Final image has both `git` and `gh`.

- [ ] **Step 3: Verify binaries are present**

Run: `docker run --rm vllm-agent-test bash -lc 'git --version && gh --version'`
Expected: prints versions for both, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add compose/vllm-agent/Dockerfile
git commit -m "feat(compose): install git and gh in vllm-agent image"
```

---

## Task 9: Document config + update example

**Files:**
- Modify: `mcp-server/example-mcp-config.json`
- Modify: `README.md`

- [ ] **Step 1: Read current example config**

Run: `cat mcp-server/example-mcp-config.json`
Expected: shows current env keys.

- [ ] **Step 2: Update example config**

In `mcp-server/example-mcp-config.json`, add three keys to the `env` object alongside the existing ones (preserve existing keys, do not remove):

```json
"GITHUB_TOKEN": "ghp_replace_with_your_personal_access_token",
"GIT_AUTHOR_NAME": "Your Name",
"GIT_AUTHOR_EMAIL": "you@example.com"
```

- [ ] **Step 3: Add a README note**

In `README.md`, find the section that describes `.mcp.json` configuration (search for `mcp.json` or `VLLM_API_KEY`) and append a short paragraph:

```markdown
### GitHub PAT pass-through

To let the worker run `git`/`gh` commands authenticated as you, add these
optional keys to the `env` block in `.mcp.json`:

- `GITHUB_TOKEN` — a fine-grained or classic PAT with the scopes you need
  (typically `repo`).
- `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` — used by `git commit` when no local
  `user.name`/`user.email` is configured.

The MCP server reads these from its own environment and forwards them per
request as an `env_overlay`. The worker exports them into the `bash` subprocess
environment only; nothing is written to the VM. The transcript writer redacts
the token from JSONL records to prevent accidental disclosure.
```

- [ ] **Step 4: Commit**

```bash
git add mcp-server/example-mcp-config.json README.md
git commit -m "docs: GitHub PAT pass-through config keys"
```

---

## Task 10: End-to-end smoke test (manual)

**Files:** none (manual verification step)

- [ ] **Step 1: Rebuild VM image**

Run (on the host):
```bash
docker compose -f compose.yaml build vllm-agent
docker compose -f compose.yaml up -d vllm-agent nginx
```
Expected: vllm-agent container restarts cleanly.

- [ ] **Step 2: Set PAT locally**

Edit `.mcp.json`, add a valid `GITHUB_TOKEN`. Restart the MCP host (e.g. reload Claude Code so it re-spawns the MCP server with the new env).

- [ ] **Step 3: Dispatch a token-aware test task**

From Claude Code, issue:
> Use `agent_run` (mode=remote) with task: "Run `gh auth status` and report the result."

Expected: in `summary.md` (under the returned `out_dir`), `gh auth status` reports authenticated as the PAT owner.

- [ ] **Step 4: Verify transcript redaction**

Run (on the host): `grep -c 'GITHUB_TOKEN\|ghp_' <out_dir>/transcript.jsonl || true`
Expected: 0 matches for the literal token value (the env var *name* `GITHUB_TOKEN` may appear; only the *value* must be redacted).

- [ ] **Step 5: No commit** (manual verification only)

---

## Self-review notes

- **Spec coverage:** Tasks 1–9 map to spec sections 1 (container) → Task 8; 2 (MCP) → Task 7; 3 (HTTP API) → Tasks 4, 5, 6; 4 (ToolContext + bash) → Tasks 2, 3; 5 (redaction) → Task 1; 6 (`.mcp.json`) → Task 9; security & failure-modes are validated by Task 10.
- **No placeholders:** every code step contains the exact code to write; every command has a concrete expected outcome.
- **Type consistency:** `env_overlay: dict[str, str] | None` is the request-side wire type (Pydantic/dataclass-friendly); inside `agent_run`/`agent_session_step` it is normalized via `dict(... or {})` before being stored on the `Session` or passed to `ToolContext.env_overlay` (which is `dict[str, str]`). The `Transcript.redact_values` argument is a `list[str]`.
