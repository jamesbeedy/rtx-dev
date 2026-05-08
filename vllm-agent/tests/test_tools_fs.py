import pytest
from vllm_agent.tools.fs import read_file_tool, write_file_tool
from vllm_agent.tools import ToolContext
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript


@pytest.fixture
def ctx(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    return ToolContext(workspace=ws, transcript=Transcript(tmp_path / "t.jsonl"), env={})


async def test_read_file_returns_content(tmp_path, ctx):
    p = tmp_path / "hello.txt"
    p.write_text("hi there\nline 2\n")
    out = await read_file_tool.execute({"path": "hello.txt"}, ctx)
    assert out["content"] == "hi there\nline 2\n"
    assert out["path"] == str(p.resolve())


async def test_read_file_offset_limit(tmp_path, ctx):
    p = tmp_path / "many.txt"
    p.write_text("\n".join(f"line {i}" for i in range(10)))
    out = await read_file_tool.execute({"path": "many.txt", "offset": 2, "limit": 3}, ctx)
    lines = out["content"].splitlines()
    assert lines == ["line 2", "line 3", "line 4"]


async def test_read_file_missing(tmp_path, ctx):
    out = await read_file_tool.execute({"path": "nope.txt"}, ctx)
    assert "error" in out


async def test_write_file_creates_file(tmp_path, ctx):
    out = await write_file_tool.execute(
        {"path": "new/sub/x.txt", "content": "hello"}, ctx)
    assert (tmp_path / "new" / "sub" / "x.txt").read_text() == "hello"
    assert out["bytes_written"] == 5


async def test_write_file_overwrites(tmp_path, ctx):
    (tmp_path / "x.txt").write_text("old")
    await write_file_tool.execute({"path": "x.txt", "content": "new"}, ctx)
    assert (tmp_path / "x.txt").read_text() == "new"
