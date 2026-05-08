from pathlib import Path
import pytest
from vllm_agent.workspace import Workspace


def test_default_workdir_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = Workspace.resolve(None)
    assert ws.root == tmp_path.resolve()


def test_explicit_workdir(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    assert ws.root == tmp_path.resolve()


def test_workdir_must_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        Workspace.resolve(str(tmp_path / "does-not-exist"))


def test_is_inside(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    assert ws.is_inside(tmp_path / "a.txt") is True
    assert ws.is_inside(tmp_path / "sub" / "b.txt") is True
    assert ws.is_inside(Path("/etc/passwd")) is False


def test_resolve_path_relative(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    assert ws.resolve_path("a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_resolve_path_absolute_outside_returns_as_is(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    p = ws.resolve_path("/etc/hosts")
    assert p == Path("/etc/hosts")
