import pytest
from vllm_agent.tools.fs import read_file_tool, write_file_tool, edit_file_tool, grep_tool, glob_tool
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


async def test_edit_file_basic(tmp_path, ctx):
    p = tmp_path / "f.txt"
    p.write_text("foo bar baz")
    out = await edit_file_tool.execute(
        {"path": "f.txt", "old": "bar", "new": "BAR"}, ctx)
    assert p.read_text() == "foo BAR baz"
    assert out["replacements"] == 1


async def test_edit_file_old_not_unique_errors(tmp_path, ctx):
    p = tmp_path / "f.txt"
    p.write_text("x x x")
    out = await edit_file_tool.execute(
        {"path": "f.txt", "old": "x", "new": "y"}, ctx)
    assert "error" in out
    assert p.read_text() == "x x x"


async def test_edit_file_replace_all(tmp_path, ctx):
    p = tmp_path / "f.txt"
    p.write_text("x x x")
    out = await edit_file_tool.execute(
        {"path": "f.txt", "old": "x", "new": "y", "replace_all": True}, ctx)
    assert p.read_text() == "y y y"
    assert out["replacements"] == 3


async def test_edit_file_old_not_found(tmp_path, ctx):
    p = tmp_path / "f.txt"
    p.write_text("hello")
    out = await edit_file_tool.execute(
        {"path": "f.txt", "old": "missing", "new": "x"}, ctx)
    assert "error" in out


async def test_grep_basic(tmp_path, ctx):
    (tmp_path / "a.py").write_text("def hello():\n    return 1\n")
    (tmp_path / "b.py").write_text("def world():\n    return 2\n")
    out = await grep_tool.execute({"pattern": "return"}, ctx)
    paths = {m["path"] for m in out["matches"]}
    assert any(p.endswith("a.py") for p in paths)
    assert any(p.endswith("b.py") for p in paths)


async def test_grep_with_glob(tmp_path, ctx):
    (tmp_path / "a.py").write_text("hit\n")
    (tmp_path / "a.txt").write_text("hit\n")
    out = await grep_tool.execute({"pattern": "hit", "glob": "*.py"}, ctx)
    paths = {m["path"] for m in out["matches"]}
    assert any(p.endswith("a.py") for p in paths)
    assert not any(p.endswith("a.txt") for p in paths)


async def test_grep_no_matches(tmp_path, ctx):
    (tmp_path / "a.txt").write_text("nothing here\n")
    out = await grep_tool.execute({"pattern": "missing"}, ctx)
    assert out["matches"] == []


async def test_glob_basic(tmp_path, ctx):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    out = await glob_tool.execute({"pattern": "*.py"}, ctx)
    names = sorted(p.rsplit("/", 1)[-1] for p in out["paths"])
    assert names == ["a.py", "b.py"]


async def test_glob_recursive(tmp_path, ctx):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("")
    out = await glob_tool.execute({"pattern": "**/*.py"}, ctx)
    assert any(p.endswith("sub/deep.py") for p in out["paths"])
