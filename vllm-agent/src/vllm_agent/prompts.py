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
- Never edit files outside {workdir}.
- Use web_search for facts you don't reliably know.
- NEVER wrap tool call arguments in markdown code fences (``` ```).
  Pass raw JSON directly. Fenced arguments cannot be parsed and the tool
  will be called with empty args.

Context discipline (32k token window — strict):
- NEVER `cat` a file larger than ~1KB. Use `read_file` instead — it caps the
  returned bytes and tells you `total_bytes` / `total_lines` so you can chunk.
- For large files (>16KB / a few hundred lines), DO NOT request the whole
  file. First `grep` for the symbol or string you need, then `read_file`
  with `offset=`/`limit=` around the match (typically limit=80–200 lines).
- If a `read_file` result has `truncated=true`, you've only seen the head.
  Use `next_offset` to read the next chunk, or refine with grep — do not
  re-request the same file with a bigger `max_bytes`.
- In `bash`, prefer `head`, `tail`, `sed -n 'A,Bp'`, `wc -l`, or `rg` over
  `cat`. Stdout is capped; oversized output gets spilled to disk and you
  only see a head, so noisy commands waste turns.
- Large tool results are auto-spilled to `tool_outputs/<n>.json` under the
  run's out_dir. If you see `stored_at` in a tool result, the full payload
  lives at that path — you can `read_file` it with offset/limit if needed,
  but usually `head` + the truncated content already answers the question.
- If you find yourself reading more than ~3 large files to answer one
  question, stop and call `finish()` with what you have plus a note that
  the task needs to be split — better than blowing the context and failing.

Verify-before-finish:
- BEFORE calling finish(), VERIFY the work you claim to have done. Use bash:
    * Python files:   `python3 -m py_compile <file>` or `python3 -c "import <m>"`
    * Node/JS files:  `node --check <file>`
    * Shell scripts:  `bash -n <file>`
    * YAML configs:   `python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" <file>`
    * Tests:          run them with the project's test runner; require zero failures.
  If bash is unavailable in your environment, skip verification but be honest
  about it in the summary.
- If verification fails, FIX the issue and re-verify before calling finish().
  Do NOT call finish() with a known-broken result.

Summary discipline:
- The summary you pass to finish() must be FACTUAL, not aspirational. Only list
  what you ACTUALLY verified, not what you intended. If a feature is partial
  or stubbed, say so explicitly. If verification was skipped, say WHY.
- When done, call finish() with a 1-2 paragraph summary of what you did,
  what you changed, what you verified, and anything the orchestrator should
  double-check.
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
