import json
import os
import pytest
import respx
from httpx import Response
from vllm_agent.api import agent_run, AgentRunRequest


def _resp(content=None, tool_calls=None):
    msg = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


@respx.mock
async def test_agent_run_writes_summary(tmp_path, monkeypatch):
    """End-to-end: agent_run produces summary.md, transcript.jsonl, files_changed.txt."""
    # First reply: write a file. Second reply: finish.
    seq = [
        _resp(tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"path": "out.txt", "content": "hi"})},
        }]),
        _resp(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "finish",
                         "arguments": json.dumps({"summary": "wrote out.txt"})},
        }]),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=seq[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    out_dir = tmp_path / "out"
    req = AgentRunRequest(
        task="write hi to out.txt then finish",
        skill=None,
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(out_dir),
        max_iterations=5,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    assert (out_dir / "summary.md").read_text() == "wrote out.txt"
    assert (out_dir / "transcript.jsonl").exists()
    assert "hi" in (tmp_path / "out.txt").read_text()
    assert "out.txt" in (out_dir / "files_changed.txt").read_text()


import json as _json


@respx.mock
async def test_agent_session_start_step_status_stop(tmp_path, monkeypatch):
    """Multi-step session: start, run one step that calls finish, then stop."""
    seq = [
        _resp(tool_calls=[{
            "id": "c", "type": "function",
            "function": {"name": "finish",
                         "arguments": _json.dumps({"summary": "one-step done"})},
        }]),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=seq[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))

    from vllm_agent.api import agent_session_start, agent_session_step, agent_session_status, agent_session_stop, AgentSessionStartRequest
    s = await agent_session_start(AgentSessionStartRequest(
        goal="do a thing", skill=None, mode="remote", workdir=str(tmp_path)))
    assert s.status == "running"

    step = await agent_session_step(s.session_id, nudge=None, max_iterations=3)
    assert step.status == "completed"
    assert step.step_status == "ok"
    assert step.iterations_this_step == 1

    status = await agent_session_status(s.session_id)
    assert status.iterations_total == 1

    stopped = await agent_session_stop(s.session_id)
    assert stopped.status == "stopped"


@respx.mock
async def test_agent_run_emits_timeout_status(tmp_path, monkeypatch):
    """If the run exceeds timeout_s, status is 'timeout'."""
    import asyncio as _asyncio

    async def _slow(_request):
        await _asyncio.sleep(2.0)
        return Response(200, json={"choices": [{"message": {"content": "late"}}]})

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_slow)
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    req = AgentRunRequest(
        task="anything",
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        max_iterations=1,
        timeout_s=1,
    )
    result = await agent_run(req)
    assert result.status == "timeout"
    assert result.error and "timeout" in result.error.lower()


@respx.mock
async def test_agent_run_populates_search_log(tmp_path, monkeypatch):
    """If the worker calls web_search during a run, search_log lists the queries."""
    seq = [
        _resp(tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "web_search",
                         "arguments": json.dumps({"query": "test query"})},
        }]),
        _resp(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "finish",
                         "arguments": json.dumps({"summary": "did a search"})},
        }]),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=seq[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=Response(200, text="<html></html>"))

    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    req = AgentRunRequest(
        task="search for something",
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        max_iterations=3,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    assert len(result.search_log) == 1
    assert result.search_log[0]["query"] == "test query"


@respx.mock
async def test_agent_session_step_returns_step_status(tmp_path, monkeypatch):
    """agent_session_step returns BOTH lifecycle status and per-step run status."""
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "done", "tool_calls": []}}]
        })
    )
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))

    from vllm_agent.api import (
        agent_session_start, agent_session_step,
        AgentSessionStartRequest,
    )
    s = await agent_session_start(AgentSessionStartRequest(
        goal="g", workdir=str(tmp_path)))
    step = await agent_session_step(s.session_id, max_iterations=2)
    assert step.status == "completed"
    assert step.step_status == "ok"


@respx.mock
async def test_agent_run_captures_diff_when_git_repo(tmp_path, monkeypatch):
    """If workdir is a git repo, agent_run writes diff.patch and sets diff_path."""
    import subprocess as sp

    # Set up a tiny git repo with one committed file and one uncommitted edit.
    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    sp.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("original\n")
    sp.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    sp.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    # Worker writes a new file (which will appear in `git diff`).
    seq = [
        _resp(tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"path": "g.txt", "content": "new file\n"})},
        }]),
        _resp(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "finish",
                         "arguments": json.dumps({"summary": "wrote g.txt"})},
        }]),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=seq[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    out_dir = tmp_path / "out"
    req = AgentRunRequest(
        task="write g.txt", mode="remote",
        workdir=str(tmp_path), out_dir=str(out_dir),
        max_iterations=5, max_tokens=512,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    assert result.diff_path == str(out_dir / "diff.patch")
    # New untracked file won't show in `git diff` (unstaged-untracked is not in the diff).
    # But the diff command should at least succeed and produce an empty file or short output.
    # Verify the file exists and is readable.
    assert (out_dir / "diff.patch").exists()


@respx.mock
async def test_agent_run_no_diff_when_not_git(tmp_path, monkeypatch):
    """If workdir is NOT a git repo, diff_path is None."""
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    req = AgentRunRequest(
        task="anything", mode="remote",
        workdir=str(tmp_path), out_dir=str(tmp_path / "out"),
        max_iterations=1, max_tokens=64,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    assert result.diff_path is None


@respx.mock
async def test_agent_run_uses_skill_content_when_provided(tmp_path, monkeypatch):
    """If skill_content is set in the request, the worker prompt includes it
    and SkillLoader is NOT consulted (we use a fake skill name to verify)."""
    captured: dict = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_capture)
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    req = AgentRunRequest(
        task="anything",
        skill="ignored:does-not-exist",
        skill_content="--- PROVIDED SKILL CONTENT ---",
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        max_iterations=1,
        max_tokens=64,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    sys_msg = next(m for m in captured["body"]["messages"] if m["role"] == "system")
    assert "PROVIDED SKILL CONTENT" in sys_msg["content"]


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


@respx.mock
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
    # Transcript should have redacted the token.
    transcript_text = (tmp_path / "out" / "transcript.jsonl").read_text()
    assert "ghp_overlaytest_98765" not in transcript_text
