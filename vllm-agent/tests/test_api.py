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
