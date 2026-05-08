from typer.testing import CliRunner
from vllm_agent.cli import app


def test_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "serve" in result.stdout
    assert "list-skills" in result.stdout


def test_list_skills_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["list-skills"])
    assert result.exit_code == 0
    # No skills installed in fresh HOME — output is just an empty list / header.
