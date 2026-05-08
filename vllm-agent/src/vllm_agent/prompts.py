"""Construct system + user prompts for the agent loop, with context budget enforcement.

Rough char-to-token ratio: 4 chars/token. 32k context = 128k chars total budget.
We reserve ~32k chars for completion + working transcript, leaving ~96k for
system + initial user prompt. Defaults below assume that envelope.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_BUDGET_CHARS = 96_000   # ~24k tokens for system + user prompt
DEFAULT_CONTEXT_CHARS = 60_000  # cap for `extra_context` body


class PromptBudgetError(Exception):
    pass


_WORKER_TAIL = """
You are the vllm-rtx5090 worker. You have these tools:
  read_file, write_file, edit_file, bash, grep, glob, web_search, finish

Workspace: {workdir}
Mode: {mode}

Discipline:
- Edit files in place via edit_file/write_file. Run tests via bash.
- Iterate until the task is done or you hit a blocker.
- When done, call finish() with a 1-2 paragraph summary of what you did,
  what you changed, and anything the orchestrator should verify.
- Never edit files outside {workdir}.
- Use web_search for facts you don't reliably know.
"""


def build_system_prompt(
    skill_content: str | None,
    workdir: str,
    mode: str,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> str:
    body = _WORKER_TAIL.format(workdir=workdir, mode=mode)
    if skill_content:
        body = skill_content.rstrip() + "\n\n" + body
    if len(body) > budget_chars:
        raise PromptBudgetError(
            f"system prompt is {len(body)} chars; budget is {budget_chars}. "
            "Use a shorter skill or raise the budget.")
    return body


def build_user_prompt(
    task: str,
    extra_context_paths: list[str] | None = None,
    max_context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> str:
    parts: list[str] = [f"Task:\n{task}\n"]
    used = 0
    if extra_context_paths:
        parts.append("\nExtra context (pre-loaded files):\n")
        for p in extra_context_paths:
            try:
                text = Path(p).read_text()
            except (FileNotFoundError, PermissionError, IsADirectoryError) as e:
                parts.append(f"--- {p} ---\n[could not read: {e}]\n")
                continue
            remaining = max_context_chars - used
            if remaining <= 0:
                parts.append(f"--- {p} ---\n[truncated: budget exhausted]\n")
                continue
            if len(text) > remaining:
                parts.append(f"--- {p} (first {remaining} chars; truncated) ---\n"
                             f"{text[:remaining]}\n[truncated]\n")
                used = max_context_chars
            else:
                parts.append(f"--- {p} ---\n{text}\n")
                used += len(text)
    return "".join(parts)
