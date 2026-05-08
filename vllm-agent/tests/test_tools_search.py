import pytest
import respx
from httpx import Response
from vllm_agent.tools.search import web_search_tool
from vllm_agent.tools import ToolContext
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript


@pytest.fixture
def ctx(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    return ToolContext(workspace=ws, transcript=Transcript(tmp_path / "t.jsonl"), env={})


_FAKE_HTML = """
<html><body>
  <div class="result">
    <a class="result__a" href="https://example.com/x">Example X</a>
    <div class="result__snippet">snippet for x</div>
  </div>
  <div class="result">
    <a class="result__a" href="https://example.com/y">Example Y</a>
    <div class="result__snippet">snippet for y</div>
  </div>
</body></html>
"""


@respx.mock
async def test_web_search_parses_results(ctx):
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=Response(200, text=_FAKE_HTML))
    out = await web_search_tool.execute({"query": "example"}, ctx)
    assert len(out["results"]) == 2
    assert out["results"][0]["title"] == "Example X"
    assert out["results"][0]["url"].startswith("https://example.com/x")


@respx.mock
async def test_web_search_handles_http_error(ctx):
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=Response(503, text="oops"))
    out = await web_search_tool.execute({"query": "example"}, ctx)
    assert "error" in out
