"""Live smoke test against the real vLLM endpoint. Opt-in via `pytest -m live`."""
import os
import pytest
from vllm_agent.api import AgentRunRequest, agent_run

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("VLLM_BASE_URL"),
                    reason="VLLM_BASE_URL not set")
async def test_live_simple_finish(tmp_path):
    """Hit the real vLLM and ask the worker to call finish()."""
    req = AgentRunRequest(
        task="Call the finish() tool with the summary 'live ping ok'. "
             "Do not use any other tools.",
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        max_iterations=3,
        max_tokens=512,
        temperature=0.0,
        timeout_s=120,
    )
    result = await agent_run(req)
    assert result.status == "ok", result.error
    summary = (tmp_path / "out" / "summary.md").read_text()
    assert "live ping ok" in summary or len(summary) > 0
