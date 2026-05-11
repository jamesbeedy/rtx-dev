# MCP Tools Reference

## Tool List

### Core tools

| Tool | Purpose |
|------|---------|
| `health` | Probe vLLM endpoint |
| `list_models` | List served models |
| `verify_project` | Smoke-test a Python project (charm or pyproject) |
| `ask` | Single-turn Q&A with web search; writes answer to disk |
| `converse` | Multi-turn dialog; writes final reply to disk |
| `critique` | Take a draft, produce a corrected version |
| `scaffold` | Multi-file project generation; parses FILE blocks |

### Agent tools

| Tool | Purpose |
|------|---------|
| `list_skills` | List available skills (project / user / superpowers) |
| `agent_run` | Dispatch a one-shot coding-agent task |
| `agent_session_start` | Start a long-running session |
| `agent_session_step` | Run one step of a session |
| `agent_session_status` | Get session state |
| `agent_session_stop` | Stop a session |
| `agent_run_artifacts` | Read back artifacts (summary, files changed, transcript) of a completed run |

## Mode Selection

All `agent_*` tools take a `mode` parameter:

- **`mode="remote"`** (default): worker tools execute inside the VM
  (disposable sandbox; full Bash). Requires `VLLM_AGENT_URL` to be set.
- **`mode="local"`**: worker tools execute on your machine. Requires
  `VLLM_AGENT_LOCAL_BASH=1` to enable bash (off by default — bash on your real
  filesystem is dangerous).

Guidance:

- **Quick read-heavy / generate-only tasks** (review, audit, draft a doc): `local`
- **Long autonomous coding work** (write code, run tests, fix, repeat): `remote`
- **Anything destructive or that runs package installs**: `remote` (VM is the sandbox)
- **Default**: `remote`

## Output Discipline

`agent_run` and `agent_session_step` never return raw model output to Claude.
Everything is written to `out_dir`:

- `transcript.jsonl` — full message history with tool calls
- `summary.md` — the worker's finish() summary
- `files_changed.txt` — list of files the worker touched
- `diff.patch` — git diff (remote mode only)

Claude gets only paths + metadata. To see actual content, `Read` the file directly.

## GitHub PAT Pass-through

To let the remote worker run `git` and `gh` commands authenticated as you,
add these optional keys to the `env` block in your MCP settings:

- `GITHUB_TOKEN` — a fine-grained or classic PAT with the scopes you need
  (typically `repo`).
- `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` — used by `git commit` when no local
  `user.name` / `user.email` is configured.

The MCP server reads these from its own environment and forwards them on every
agent dispatch as an `env_overlay` field. The vllm-agent worker exports the
overlay into the `bash` subprocess environment only — nothing is written to
the VM. The transcript writer redacts the token from JSONL records to
prevent accidental disclosure.

## Skills

Pass `skill=` to `agent_run` to prepend a skill's full content to the worker's
system prompt:

```
agent_run(skill="superpowers:test-driven-development", task="...")
agent_run(skill="superpowers:systematic-debugging", task="...")
```

Use `list_skills` to discover what's available.

Skill roots, in priority order:

1. `./skills/` (project-local)
2. `~/.claude/skills/` (user)
3. `~/.claude/plugins/cache/claude-plugins-official/` (superpowers)
