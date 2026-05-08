from pathlib import Path
import pytest
from vllm_agent.prompts import build_system_prompt, build_user_prompt, PromptBudgetError


def test_build_system_prompt_no_skill():
    out = build_system_prompt(skill_content=None, workdir="/tmp/x", mode="local")
    assert "vllm-rtx5090 worker" in out
    assert "/tmp/x" in out
    assert "Mode: local" in out


def test_build_system_prompt_with_skill():
    out = build_system_prompt(
        skill_content="---\nname: tdd\n---\n\nthe full skill body",
        workdir="/tmp/x", mode="remote")
    assert "the full skill body" in out
    assert "vllm-rtx5090 worker" in out


def test_build_user_prompt_with_extra_context(tmp_path):
    f = tmp_path / "ctx.txt"
    f.write_text("contents of ctx")
    out = build_user_prompt(task="do X", extra_context_paths=[str(f)])
    assert "do X" in out
    assert "contents of ctx" in out
    assert "ctx.txt" in out


def test_user_prompt_truncates_oversized_context(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 200_000)  # 200k chars
    out = build_user_prompt(task="do X", extra_context_paths=[str(big)],
                            max_context_chars=10_000)
    assert "[truncated" in out
    assert len(out) < 12_000


def test_budget_check_raises_when_too_large():
    huge = "x" * 200_000
    with pytest.raises(PromptBudgetError):
        build_system_prompt(skill_content=huge, workdir="/tmp/x", mode="local",
                            budget_chars=50_000)


def test_system_prompt_includes_verify_discipline():
    out = build_system_prompt(skill_content=None, workdir="/tmp", mode="remote")
    # Worker is told to verify before finish, with concrete commands.
    assert "VERIFY" in out or "verify" in out.lower()
    assert "node --check" in out or "py_compile" in out or "bash -n" in out
    # Summary discipline: factual, not aspirational.
    assert "FACTUAL" in out or "factual" in out.lower() or "actually" in out.lower()
