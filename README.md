# rtx-inference

Self-hosted Qwen3-Coder-30B-A3B vLLM endpoint behind an MCP server, with a
companion agent runtime (`vllm-agent`) so Claude Code can offload bulk codegen
and iterative coding work to the local 5090.

## Components

- **vLLM** — runs in an LXD VM with GPU passthrough, serves an OpenAI-compatible
  chat-completions endpoint.
- **vllm-agent** — a Python runtime that wraps vLLM with worker tools
  (read_file, write_file, edit_file, bash, grep, glob, web_search, finish), a
  tool-calling loop, skill loading, and session storage. Runs both as a Python
  library and as a FastAPI HTTP server (`vllm-agent serve`) inside the VM.
- **mcp-server** — the MCP shim that exposes vllm-agent's capabilities to
  Claude Code.

## Provisioning

```bash
./launch-inference.sh --lxd-host USER@LXD-CLUSTER-MEMBER
```

This:
1. Creates an LXD VM with GPU passthrough.
2. Installs vLLM and serves it on port 8000.
3. Installs `vllm-agent serve` as a systemd unit on port 8088.
4. Updates `.mcp.json` with the VM's URLs (`VLLM_BASE_URL`, `VLLM_AGENT_URL`).

After it returns, restart Claude Code (or reload the MCP server) and the new
tools below will be available.

## MCP tools

### Existing (Plan A, refactored to use vllm-agent internally)

| Tool | Purpose |
|------|---------|
| `health` | Probe vLLM endpoint |
| `list_models` | List served models |
| `verify_project` | Smoke-test a Python project (charm or pyproject) |
| `ask` | Single-turn Q&A with web search; writes answer to disk |
| `converse` | Multi-turn dialog; writes final reply to disk |
| `critique` | Take a draft, produce a corrected version |
| `scaffold` | Multi-file project generation; parses FILE blocks |

### New (Plan B)

| Tool | Purpose |
|------|---------|
| `list_skills` | List available skills (project / user / superpowers) |
| `agent_run` | Dispatch a one-shot coding-agent task |
| `agent_session_start` | Start a long-running session |
| `agent_session_step` | Run one step of a session |
| `agent_session_status` | Get session state |
| `agent_session_stop` | Stop a session |

All `agent_*` tools take a `mode` parameter:
- **`mode="remote"`** (default): worker tools execute inside the VM
  (disposable sandbox; full Bash). Requires `VLLM_AGENT_URL` to be set.
- **`mode="local"`**: worker tools execute on your machine. Requires
  `VLLM_AGENT_LOCAL_BASH=1` to enable bash (off by default — bash on your real
  filesystem is dangerous).

### Output discipline

`agent_run` and `agent_session_step` never return raw model output to Claude.
Everything is written to `out_dir`:
- `transcript.jsonl` — full message history with tool calls
- `summary.md` — the worker's finish() summary
- `files_changed.txt` — list of files the worker touched
- `diff.patch` — git diff (Plan B+; remote mode only)

Claude gets only paths + metadata. To see actual content, Claude `Read`s the
file itself.

## Mode selection guidance

- **Quick read-heavy / generate-only tasks** (review, audit, draft a doc): `local`.
- **Long autonomous coding work** (write code, run tests, fix, repeat): `remote`.
- **Anything destructive or that runs package installs**: `remote` (the VM is
  the sandbox).
- **Default**: `remote`.

## Skills

`agent_run(skill="superpowers:test-driven-development", task="...")` loads the
full SKILL.md content and prepends it to the worker's system prompt. Use
`list_skills` to discover what's available.

Skill roots, in priority order:
1. `./skills/` (project-local)
2. `~/.claude/skills/` (user)
3. `~/.claude/plugins/cache/claude-plugins-official/` (superpowers)

## Repository layout

```
rtx_5090_dev/
├── vllm-agent/                         # standalone runtime package
├── mcp-server/                         # MCP stdio shim
├── profiles/rtx-inference.yaml.tpl     # cloud-init for the LXD VM
├── launch-inference.sh                 # provisioning script
├── docs/superpowers/                   # design specs + plans
├── .mcp.json                           # MCP server registration
└── README.md
```
