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
