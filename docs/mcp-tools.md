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

## Giving the worker its own MCP tools

The vllm-agent worker can act as an MCP **client**, calling out to external
MCP servers (filesystem, GitHub, your own internal MCPs) alongside its
built-in tools (`read_file`, `write_file`, `edit_file`, `bash`, `grep`,
`glob`, `web_search`, `finish`).

> **Full guide**: see [worker-mcp-clients.md](./worker-mcp-clients.md) for
> the end-to-end user story, configuration reference, auth patterns,
> common server recipes, observability, and troubleshooting. The summary
> below is enough to get a basic setup running.

### Config shape

Standard `mcpServers` JSON (compatible with Claude Code / Cursor / Continue):

```json
{
  "mcpServers": {
    "fs": {
      "command": "uvx",
      "args": ["mcp-server-filesystem", "/srv/data"]
    },
    "gh": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "ghp_..."},
      "enabled_tools": ["list_issues", "create_issue"]
    },
    "internal": {
      "transport": "sse",
      "url": "https://mcp.example.internal/sse",
      "headers": {"Authorization": "Bearer ..."}
    }
  }
}
```

Per-server keys:

| Key | Purpose |
|-----|---------|
| `command` / `args` | stdio transport — subprocess to spawn |
| `env` | extra env vars merged into the subprocess; values auto-redacted in transcripts |
| `transport` | `stdio` (default), `sse`, or `http` / `streamable-http` |
| `url` / `headers` | for `sse` / `http` transports |
| `enabled_tools` | optional allowlist of tool names from this server; omit = all |

### How to pass it in

Resolution order (first non-empty wins):

1. `agent_run(mcp_config_json=...)` / `agent_session_start(mcp_config_json=...)` — inline JSON string.
2. `agent_run(mcp_config_path=...)` — path on the **orchestrator** host. Auto-inlined into the request for `mode="remote"` so the VM can read it without seeing your host filesystem.
3. Env on the MCP server (forwarded by `_resolve_mcp_config`):
   - `VLLM_AGENT_MCP_CONFIG_JSON` — inline JSON.
   - `VLLM_AGENT_MCP_CONFIG` — path.
4. Worker-side fallback (mostly relevant in local mode): `~/.config/vllm-agent/mcp.json`.

Add to your `.mcp.json` `env` block to set a default for every dispatch:

```json
"env": {
  "VLLM_BASE_URL": "...",
  "VLLM_AGENT_URL": "...",
  "VLLM_AGENT_MCP_CONFIG": "/home/you/.config/vllm-agent/mcp.json"
}
```

### Worker-side tool naming

Each remote tool is exposed to the worker as `mcp__<server>__<tool>` (same
convention Claude Code uses). The worker's system prompt is auto-augmented
with a capped index of the discovered tools so the model knows they exist.

### Lifecycle

The MCP registry is per-`agent_run` (and per `agent_session_step` for
sessions). Stdio subprocesses are spawned at the start of the run and
shut down in a `finally` block, so a crashing worker can't leave orphans.
Session config is persisted to `session.json` so subsequent steps reload
the identical MCP set.

### Passing secrets to MCP servers

Three patterns, all supported simultaneously — pick whichever fits your
workflow:

**Option 1 — literal in `mcp_config_json`** (simplest, no setup):
```json
{"mcpServers": {"gh": {"command": "npx", "args": ["-y","@modelcontextprotocol/server-github"],
                       "env": {"GITHUB_TOKEN": "ghp_..."}}}}
```
The token sits in the JSON you pass to `agent_run` (or in your saved config
file). `env` values are auto-appended to the transcript redact list.

**Option 2 — `${VAR}` reference + `MCP_*` prefix forwarding** (recommended,
no literal secrets in the config):
```json
{"mcpServers": {
  "slack": {"command": "npx", "args": ["-y","@modelcontextprotocol/server-slack"],
            "env": {"SLACK_TOKEN": "${MCP_SLACK_TOKEN}"}},
  "remote-api": {"transport": "sse", "url": "https://mcp.example/sse",
                 "headers": {"Authorization": "Bearer ${MCP_API_KEY:-fallback}"}}
}}
```
Then in your host `.mcp.json` (the file Claude Code reads), set the real
secret on the **vllm-rtx5090** server's `env` block:
```json
"env": {
  "VLLM_AGENT_URL": "...",
  "VLLM_AGENT_API_KEY": "...",
  "MCP_SLACK_TOKEN": "xoxb-real-token",
  "MCP_API_KEY": "abc..."
}
```
The front MCP server auto-forwards any env var matching `MCP_*` (plus
the existing `GITHUB_TOKEN` / `GIT_AUTHOR_*` allowlist) to the worker via
`env_overlay`. The worker substitutes `${VAR}` in each server's `env` /
`headers` values, then merges the result into the subprocess env (stdio
transport) or into outbound request headers (SSE / HTTP transport).
Supports `${VAR}` and `${VAR:-default}`. Resolved values land in the
transcript redact list, the literal `${...}` placeholder does not.

**Option 3 — inherited env, no per-server `env` block**:
```json
{"mcpServers": {"gh": {"command": "npx", "args": ["-y","@modelcontextprotocol/server-github"]}}}
```
If the upstream MCP looks up its credentials from a well-known env name
(e.g. `GITHUB_TOKEN`) and that var is already in the worker's
`env_overlay`, the stdio subprocess inherits it automatically — no `env`
key needed in the JSON. Add custom names to the worker overlay by
prefixing them with `MCP_`.

### Security

- The worker runs MCP subprocesses **in the same trust boundary as `bash`**
  — in remote mode that's the VM, in local mode that's your machine.
- Secrets travel as **env vars** (`env`) or **HTTP headers** (`headers`),
  never `args`. `args` show in `ps -ef` and tool-call records; env/headers
  do not.
- `env` and `headers` values (literal **or** `${VAR}`-expanded) are
  appended to the transcript redact list so tokens never appear in
  `transcript.jsonl`.
- Front MCP forwards only allowlisted vars (`GITHUB_TOKEN`,
  `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`) plus any var prefixed `MCP_`.
  Anything else stays on your host.
- Schema bloat counts against the 32k context window. Use `enabled_tools`
  to trim verbose servers.

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
