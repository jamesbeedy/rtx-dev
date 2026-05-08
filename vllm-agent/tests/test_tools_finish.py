from pathlib import Path
import pytest
from vllm_agent.tools.finish import finish_tool
from vllm_agent.tools import ToolContext
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript


@pytest.fixture
def ctx(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(tmp_path / "out")},
    )
    Path(ctx.env["VLLM_AGENT_OUT_DIR"]).mkdir(parents=True, exist_ok=True)
    return ctx


async def test_finish_writes_summary(tmp_path, ctx):
    out = await finish_tool.execute({"summary": "all done\nlooks good"}, ctx)
    assert out["status"] == "finished"
    assert (Path(ctx.env["VLLM_AGENT_OUT_DIR"]) / "summary.md").read_text() == "all done\nlooks good"


async def test_finish_empty_summary_warns(tmp_path, ctx):
    out = await finish_tool.execute({"summary": ""}, ctx)
    assert out["status"] == "finished"
    assert out.get("warning")
