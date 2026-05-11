import pytest
from vllm_agent.tools.shell import bash_tool
from vllm_agent.tools import ToolContext
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript


@pytest.fixture
def ctx_local(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    return ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_MODE": "local", "VLLM_AGENT_LOCAL_BASH": "1"},
    )


@pytest.fixture
def ctx_remote(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    return ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_MODE": "remote"},
    )


async def test_bash_runs_command(ctx_remote):
    out = await bash_tool.execute({"command": "echo hello"}, ctx_remote)
    assert out["exit_code"] == 0
    assert "hello" in out["stdout"]


async def test_bash_captures_stderr(ctx_remote):
    out = await bash_tool.execute({"command": "echo oops 1>&2; exit 3"}, ctx_remote)
    assert out["exit_code"] == 3
    assert "oops" in out["stderr"]


async def test_bash_cwd_defaults_to_workspace(tmp_path, ctx_remote):
    out = await bash_tool.execute({"command": "pwd"}, ctx_remote)
    assert out["stdout"].strip() == str(tmp_path.resolve())


async def test_bash_local_blocked_without_opt_in(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_MODE": "local"},  # no VLLM_AGENT_LOCAL_BASH
    )
    out = await bash_tool.execute({"command": "echo nope"}, ctx)
    assert "error" in out
    assert "VLLM_AGENT_LOCAL_BASH" in out["error"]


async def test_bash_local_allowed_with_opt_in(ctx_local):
    out = await bash_tool.execute({"command": "echo yes"}, ctx_local)
    assert out["exit_code"] == 0
    assert "yes" in out["stdout"]


async def test_bash_timeout(ctx_remote):
    out = await bash_tool.execute(
        {"command": "sleep 5", "timeout_s": 1}, ctx_remote)
    assert "timeout" in out.get("error", "").lower() or out["exit_code"] != 0


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
