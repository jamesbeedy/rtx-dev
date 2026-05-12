import json
import pytest
import respx
from httpx import Response
from vllm_agent.loop import run_loop, LoopConfig
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript
from vllm_agent.tools import ToolContext
from vllm_agent.tools import fs as _fs  # noqa: F401 — force tool registration


def _vllm_response(content=None, tool_calls=None):
    msg = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


@respx.mock
async def test_loop_single_shot_finish(tmp_path):
    """vLLM replies with no tool_calls — loop returns immediately."""
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json=_vllm_response(content="all done")))

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )

    cfg = LoopConfig(
        vllm_base_url="https://vllm.example",
        vllm_model="qwen3-coder",
        max_iterations=5,
        max_tokens=512,
        temperature=0.2,
    )
    msgs = [{"role": "user", "content": "say hi"}]
    result = await run_loop(msgs, ctx, cfg)
    assert result.iterations == 1
    assert result.status == "ok"
    assert result.final_message_content == "all done"


@respx.mock
async def test_loop_one_tool_call_then_finish(tmp_path):
    """First reply calls read_file; second reply finishes."""
    (tmp_path / "x.txt").write_text("hello")

    responses = [
        _vllm_response(tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": "x.txt"})},
        }]),
        _vllm_response(content="read it; done"),
    ]
    counter = {"i": 0}
    def _next(_request):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=responses[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )
    cfg = LoopConfig(
        vllm_base_url="https://vllm.example",
        vllm_model="qwen3-coder",
        max_iterations=5,
        max_tokens=512,
        temperature=0.2,
    )
    result = await run_loop([{"role": "user", "content": "read x.txt"}], ctx, cfg)
    assert result.iterations == 2
    assert result.status == "ok"
    assert "read_file" in [t for t in result.tool_calls_by_name]


@respx.mock
async def test_loop_max_iterations(tmp_path):
    """vLLM keeps calling tools forever — loop bails at max_iterations."""
    (tmp_path / "x.txt").write_text("hi")
    forever = _vllm_response(tool_calls=[{
        "id": "call_loop",
        "type": "function",
        "function": {"name": "read_file",
                     "arguments": json.dumps({"path": "x.txt"})},
    }])
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json=forever))

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )
    cfg = LoopConfig(
        vllm_base_url="https://vllm.example",
        vllm_model="qwen3-coder",
        max_iterations=3,
        max_tokens=512,
        temperature=0.2,
    )
    result = await run_loop([{"role": "user", "content": "go"}], ctx, cfg)
    assert result.status == "max_iterations"
    assert result.iterations == 3


@respx.mock
async def test_loop_tool_exception_recovers(tmp_path):
    """A tool raising an exception becomes a tool-error result; loop continues."""
    responses = [
        _vllm_response(tool_calls=[{
            "id": "c1",
            "type": "function",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": "missing.txt"})},
        }]),
        _vllm_response(content="couldn't read it; giving up"),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=responses[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )
    cfg = LoopConfig(vllm_base_url="https://vllm.example",
                     vllm_model="m", max_iterations=5,
                     max_tokens=128, temperature=0)
    result = await run_loop([{"role": "user", "content": "go"}], ctx, cfg)
    assert result.status == "ok"
    # The tool call recorded the file-not-found, but the loop didn't crash.


@respx.mock
async def test_loop_vllm_5xx_retried_once(tmp_path):
    """First call fails 503, retry succeeds."""
    responses = [
        Response(503, text="oops"),
        Response(200, json=_vllm_response(content="ok now")),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return responses[i]
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(workspace=ws,
                      transcript=Transcript(out_dir / "transcript.jsonl"),
                      env={"VLLM_AGENT_OUT_DIR": str(out_dir)})
    cfg = LoopConfig(vllm_base_url="https://vllm.example",
                     vllm_model="m", max_iterations=2,
                     max_tokens=64, temperature=0)
    result = await run_loop([{"role": "user", "content": "go"}], ctx, cfg)
    assert result.status == "ok"
    assert result.final_message_content == "ok now"


@respx.mock
async def test_loop_hard_limit_bails_before_post(tmp_path, monkeypatch):
    """If the initial messages already exceed the usable budget, the loop
    must return context_exhausted without hitting vLLM."""
    import vllm_agent.loop as loop_mod
    monkeypatch.setattr(loop_mod, "CONTEXT_WINDOW_CHARS", 4_000)
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json=_vllm_response(content="should not run")))

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(workspace=ws,
                      transcript=Transcript(out_dir / "transcript.jsonl"),
                      env={"VLLM_AGENT_OUT_DIR": str(out_dir)})
    cfg = LoopConfig(vllm_base_url="https://vllm.example",
                     vllm_model="m", max_iterations=2,
                     max_tokens=128, temperature=0)
    huge = "x" * 6_000
    result = await run_loop([{"role": "user", "content": huge}], ctx, cfg)
    assert result.status == "context_exhausted"
    assert "context budget exceeded" in (result.error or "")


@respx.mock
async def test_loop_tools_subset_restricts_palette(tmp_path):
    """When tools_subset is set, only those tools are advertised to vLLM."""
    captured = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json=_vllm_response(content="done"))

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_capture)

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(workspace=ws,
                      transcript=Transcript(out_dir / "transcript.jsonl"),
                      env={"VLLM_AGENT_OUT_DIR": str(out_dir)})
    cfg = LoopConfig(vllm_base_url="https://vllm.example",
                     vllm_model="m", max_iterations=1,
                     max_tokens=64, temperature=0,
                     tools_subset=["web_search"])
    await run_loop([{"role": "user", "content": "go"}], ctx, cfg)

    advertised = {t["function"]["name"] for t in captured["body"]["tools"]}
    assert advertised == {"web_search"}
