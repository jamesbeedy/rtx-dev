# rtx-inference — Claude Code project notes

This project hosts a local Qwen3-Coder-30B vLLM endpoint and a companion agent
runtime (`vllm-agent`) exposed to you via MCP. Use it to offload heavy lifting.

## When to dispatch to vllm-rtx5090

Prefer `agent_run` (or `agent_session_*`) over doing the work yourself when:
- Bulk code generation: whole-file or whole-module drafts, scaffolds, refactors.
- Long-context analysis: read a large directory and summarize / audit / review.
- Self-iterating coding work: write code, run tests, fix failures — leave the
  whole loop on the worker side instead of bouncing back per step.

Do the work yourself (Read/Edit/Bash/etc.) when:
- The task needs design judgment or cross-file reasoning that benefits from
  Opus-class capability.
- The work is short enough that one tool call is faster than dispatching.
- You're orchestrating multi-step work that will use the worker's outputs.

## Mode selection

- `mode="remote"` (default): worker runs in the VM. Use this for anything
  long-running, destructive, or that runs package installs.
- `mode="local"`: worker runs on the user's machine. Use for quick read-only
  generate tasks. Bash requires `VLLM_AGENT_LOCAL_BASH=1` and is off by default.

## Skills

When dispatching a task that maps to a known workflow, pass `skill=`:
- `agent_run(skill="superpowers:test-driven-development", ...)` for TDD work.
- `agent_run(skill="superpowers:systematic-debugging", ...)` for bugs.
- `agent_run(skill="superpowers:writing-plans", ...)` for plan drafts.

Use `list_skills` to discover what's available.

## Output discipline

`agent_run` never returns raw model output. It returns metadata and writes
everything to `out_dir`:
- `summary.md` — worker's finish() summary
- `transcript.jsonl` — full conversation
- `files_changed.txt` — paths touched
- `diff.patch` — git diff (remote mode)

If you want to inspect the worker's output, `Read` the files in `out_dir`.
Don't paste raw transcript into the conversation.

## Existing tools (also available)

`ask` / `converse` / `critique` / `scaffold` — these were the original tools.
They now delegate to vllm-agent internally with `tools_subset=["web_search"]`,
so they behave as before but share infrastructure with the agent runtime.

The deployment is a docker compose stack inside an LXD VM (vllm, vllm-agent,
nginx) — see README for details. From the orchestrator's perspective the
tool surface is unchanged.

## Existing memory

If `~/.claude/projects/-home-bdx-allcode-github-vantagecompute-rtx-5090-dev/memory/`
has facts about the user or project, prefer those over assumptions.
