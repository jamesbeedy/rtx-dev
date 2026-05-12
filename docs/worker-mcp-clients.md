# Worker-side MCP servers

How to give the vllm-agent worker access to external MCP servers (filesystem,
GitHub, Slack, your own internal tools, anything that speaks MCP).

> **Terminology**: the vllm-rtx5090 stack already exposes its tools to Claude
> Code via an MCP server (`mcp-server/vllm_mcp.py`). This doc is about the
> *other* direction — letting the **worker** act as an MCP **client** so the
> coding agent can call out to additional MCP servers during a run. The two
> sides are decoupled; you can have either, both, or neither.

---

## 1. Why you might want this

Default worker tools are deliberately small: `read_file`, `write_file`,
`edit_file`, `bash`, `grep`, `glob`, `web_search`, `finish`. Anything beyond
that — querying your issue tracker, posting to Slack, hitting an internal
API, reading from a database, talking to Figma — needs a tool the worker
doesn't ship.

Rather than add every possible integration to the worker codebase, the
worker can connect to any number of MCP servers per run. Each server's
tools show up to the model as `mcp__<server>__<tool>` and the model calls
them like any other tool.

### User story

> "I'm dispatching a long-running task that needs to triage a GitHub repo,
> read a Postgres DB to fetch issue stats, and post a summary to Slack.
> I don't want to bake any of that into the worker — I just want to point
> it at the existing MCP servers I already have set up for Claude Code."

You can. Pass an MCP config (either inline JSON or a file path) on the
`agent_run` call. The worker spawns those servers for the run, exposes
their tools to the model, then tears them down when the run completes.

---

## 2. How it fits together

```
                    ┌──────────────────────────────────────────────┐
                    │            Claude Code (your laptop)         │
                    │                                              │
                    │   .mcp.json  ──►  vllm-rtx5090 MCP server    │
                    │                   (env: MCP_SLACK_TOKEN=...) │
                    └────────────────────┬─────────────────────────┘
                                         │ agent_run(mcp_config_json=...)
                                         │ + env_overlay (MCP_*, GITHUB_TOKEN, ...)
                                         ▼
                    ┌──────────────────────────────────────────────┐
                    │       vllm-agent worker (in VM container)    │
                    │                                              │
                    │   MCPRegistry.connect(config, env_source)    │
                    │       ├── spawn mcp__slack__* (stdio: npx)   │
                    │       ├── spawn mcp__pg__*    (stdio: uvx)   │
                    │       └── connect mcp__gh__*  (sse: https)   │
                    │                                              │
                    │   Worker loop sees built-ins + MCP tools     │
                    │   in one merged registry; calls them by      │
                    │   name. Schemas + descriptions injected      │
                    │   into the system prompt.                    │
                    └──────────────────────────────────────────────┘
```

Lifecycle: the registry is created at the start of every `agent_run` (and
every `agent_session_step`). Stdio subprocesses are torn down in a
`finally` block when the run ends, so a crashing run can't leave zombies.

---

## 3. Configuration format

Same shape as Claude Code / Cursor / Continue: a JSON object with an
`mcpServers` key.

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
      "env": {"GITHUB_TOKEN": "${MCP_GITHUB_TOKEN}"},
      "enabled_tools": ["list_issues", "create_issue", "get_issue"]
    },
    "internal": {
      "transport": "sse",
      "url": "https://mcp.example.internal/sse",
      "headers": {"Authorization": "Bearer ${MCP_INTERNAL_KEY}"}
    }
  }
}
```

### Per-server keys

| Key | Required for | Purpose |
|-----|--------------|---------|
| `command` | stdio | Executable to spawn. Must be on the worker's `PATH`. Container ships `uvx`, `npx`, `python3`, `git`, `gh`, `bash`. |
| `args` | stdio | Argument list passed to `command`. |
| `env` | optional | Extra env vars merged into the stdio subprocess. Values support `${VAR}` and `${VAR:-default}` substitution. Empty when omitted. |
| `transport` | optional | `stdio` (default), `sse`, or `http` (alias `streamable-http`). |
| `url` | sse / http | Remote endpoint URL. |
| `headers` | optional, sse / http | Outbound HTTP headers. Same `${VAR}` substitution. |
| `enabled_tools` | optional | Allowlist of tool names from this server. Omit to expose all. Use to trim noisy servers — every tool schema costs against the 32k context window. |

### Transport choice

- **stdio** — Worker spawns a subprocess and speaks MCP over its stdin/stdout. Best for tools you own or that ship as a CLI (filesystem MCP, custom Python tools via `uvx`, anything you'd otherwise install on the worker). Container has both `uvx` (Astral) and `npx` (Node 24) pre-installed, so most public MCP servers work without changes.
- **sse** — Worker opens an HTTP `text/event-stream` connection to a remote MCP server. Use for hosted MCPs, internal services, anything behind a network boundary.
- **http** / **streamable-http** — Same use cases as SSE, newer streaming protocol. Pick whichever the upstream server supports.

---

## 4. Where the config comes from

The worker resolves a config from the first source that produces one:

1. Per-call argument: `agent_run(mcp_config_json="...")` or `agent_run(mcp_config_path="/path/to/mcp.json")`.
2. Front MCP env: `VLLM_AGENT_MCP_CONFIG_JSON` (inline JSON) or `VLLM_AGENT_MCP_CONFIG` (path on the host running Claude Code).
3. Worker-side default: `~/.config/vllm-agent/mcp.json` inside the container (rarely used — paths inside the container aren't what you usually want).
4. Empty — no MCP servers; worker uses built-ins only.

For `mode="remote"` the orchestrator auto-inlines a path-based config so the
VM doesn't need to read your host filesystem. You don't need to do anything
special for this — `agent_run(mcp_config_path="...")` reads the file on your
laptop and ships the contents in the request body.

### Recipe — set once via `.mcp.json`

Edit your `.mcp.json` to give the vllm-rtx5090 server a default config path:

```json
{
  "mcpServers": {
    "vllm-rtx5090": {
      "command": "/abs/path/to/mcp-server/.venv/bin/python",
      "args": ["/abs/path/to/mcp-server/vllm_mcp.py"],
      "env": {
        "VLLM_BASE_URL": "https://your-endpoint/v1",
        "VLLM_AGENT_URL": "https://your-endpoint/agent",
        "VLLM_AGENT_API_KEY": "...",
        "VLLM_AGENT_MCP_CONFIG": "/home/you/.config/vllm-agent/mcp.json",
        "MCP_GITHUB_TOKEN": "ghp_...",
        "MCP_SLACK_TOKEN": "xoxb-..."
      }
    }
  }
}
```

Every subsequent `agent_run` picks up the config automatically; no need to
pass `mcp_config_*` per call.

### Recipe — override per call

```python
agent_run(
    task="...",
    mcp_config_json=json.dumps({
        "mcpServers": {
            "tmp": {"command": "uvx", "args": ["some-mcp-server"]}
        }
    }),
)
```

Use this when one specific run needs a server that shouldn't be the default
(e.g., a destructive ops MCP you only want available for a specific task).

---

## 5. Auth patterns

All three patterns are supported simultaneously. Pick whichever fits.

### Option 1 — Literal token in the config

Simplest. Secret lives in the JSON.

```json
{"mcpServers": {"gh": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": {"GITHUB_TOKEN": "ghp_real_token_here"}
}}}
```

Pros: no extra setup. Cons: secret literal lives wherever the JSON lives
(committed config files are a footgun). Token is auto-redacted from
`transcript.jsonl` regardless.

### Option 2 — `${VAR}` reference + `MCP_*` env forwarding (recommended)

Secrets stay in your host `.mcp.json` `env` block on the **vllm-rtx5090**
MCP server. The worker substitutes references at connect time.

Step 1 — put secrets on the vllm-rtx5090 server's env block (host `.mcp.json`):

```json
"env": {
  "VLLM_AGENT_URL": "...",
  "VLLM_AGENT_API_KEY": "...",
  "MCP_GITHUB_TOKEN": "ghp_...",
  "MCP_SLACK_TOKEN": "xoxb-...",
  "MCP_API_KEY":     "..."
}
```

Step 2 — reference them in your worker MCP config:

```json
{"mcpServers": {
  "gh":    {"command": "npx", "args": ["-y","@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "${MCP_GITHUB_TOKEN}"}},
  "slack": {"command": "npx", "args": ["-y","@modelcontextprotocol/server-slack"],
            "env": {"SLACK_TOKEN": "${MCP_SLACK_TOKEN}"}},
  "remote": {"transport": "sse", "url": "https://mcp.example/sse",
             "headers": {"Authorization": "Bearer ${MCP_API_KEY:-anon}"}}
}}
```

How it works:
- `_build_env_overlay()` (`mcp-server/vllm_mcp.py:88`) sees the
  `MCP_*` prefix and forwards each match as part of the `env_overlay`
  field on every agent dispatch.
- The worker's `agent_run` (`vllm-agent/src/vllm_agent/api.py`) passes
  `env_overlay` as `env_source=` into `MCPRegistry.connect()`.
- `_expand_dict()` (`vllm-agent/src/vllm_agent/tools/mcp_client.py`) walks
  each server's `env` and `headers`, substituting `${VAR}` references
  against `{**os.environ, **env_source}`.
- Resolved values land in `MCPRegistry.redact_values` so they never
  show up in `transcript.jsonl`.

Substitution syntax:
- `${VAR}` — replaced with the value; empty string if undefined.
- `${VAR:-default}` — replaced with the value, or `default` if undefined.

### Option 3 — Inherited env (no per-server `env` block)

If the upstream MCP looks up its credentials from a well-known env name
(e.g. `GITHUB_TOKEN`), and that var is already in the worker's
`env_overlay`, the stdio subprocess inherits it automatically:

```json
{"mcpServers": {"gh": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"]
}}}
```

Currently always-forwarded vars:
- `GITHUB_TOKEN`
- `GIT_AUTHOR_NAME`
- `GIT_AUTHOR_EMAIL`
- Anything matching `MCP_*`

Other vars stay on your host. To add a new always-forwarded var, prefix
it with `MCP_`. (Adding to `_ENV_OVERLAY_ALLOWLIST` works too but requires
a code change.)

### How they interact

You can mix all three in one config. For example:

```json
{"mcpServers": {
  "fs":       {"command": "uvx", "args": ["mcp-server-filesystem", "/srv"]},
  "gh":      {"command": "npx", "args": ["-y","@modelcontextprotocol/server-github"]},
  "slack":   {"command": "npx", "args": ["-y","@modelcontextprotocol/server-slack"],
              "env": {"SLACK_TOKEN": "${MCP_SLACK_TOKEN}"}},
  "literal": {"command": "...", "env": {"X": "literal-secret"}}
}}
```
- `fs` — no auth needed.
- `gh` — inherits `GITHUB_TOKEN` from env_overlay (option 3).
- `slack` — substitutes from `MCP_SLACK_TOKEN` env_overlay var (option 2).
- `literal` — embedded secret (option 1).

---

## 6. Tool naming and prompt injection

Every tool from an MCP server is exposed to the model as
`mcp__<server>__<tool>`. Examples:

- `mcp__fs__read_file`
- `mcp__gh__list_issues`
- `mcp__slack__post_message`

This matches the Claude Code convention so prompts that reference
`mcp__*` tool names port over without changes.

The worker's system prompt gets an auto-appended block listing every
discovered MCP tool with a one-line description (capped at ~2 KB so a
verbose server can't blow the budget). Example tail of the system prompt:

```
Additional MCP tools available (call by exact name):
  - mcp__fs__read_file: Read a file from the configured root
  - mcp__gh__list_issues: List issues in a GitHub repository
  - mcp__slack__post_message: Post a message to a channel
```

To trim this list, use `enabled_tools` on the noisy server:

```json
"gh": {
  "command": "npx", "args": ["-y","@modelcontextprotocol/server-github"],
  "enabled_tools": ["list_issues", "create_issue", "get_issue"]
}
```

---

## 7. End-to-end walkthrough

### One-time setup

1. Decide which MCP servers you want the worker to access.
2. Note any secrets the servers need (`GITHUB_TOKEN`, `SLACK_TOKEN`, etc).
3. Write the worker MCP config:

   ```bash
   mkdir -p ~/.config/vllm-agent
   $EDITOR ~/.config/vllm-agent/mcp.json
   ```

   Paste a config block (Option 2 style — easiest to maintain):

   ```json
   {
     "mcpServers": {
       "gh": {
         "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-github"],
         "env": {"GITHUB_TOKEN": "${MCP_GITHUB_TOKEN}"}
       },
       "slack": {
         "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-slack"],
         "env": {"SLACK_TOKEN": "${MCP_SLACK_TOKEN}"}
       }
     }
   }
   ```

4. Wire the path + secrets into the front MCP's env block (`.mcp.json` on
   your laptop, under `mcpServers.vllm-rtx5090.env`):

   ```json
   "env": {
     "VLLM_AGENT_URL": "...",
     "VLLM_AGENT_API_KEY": "...",
     "VLLM_AGENT_MCP_CONFIG": "/home/you/.config/vllm-agent/mcp.json",
     "MCP_GITHUB_TOKEN": "ghp_...",
     "MCP_SLACK_TOKEN": "xoxb-..."
   }
   ```

5. Restart Claude Code so the front MCP picks up the new env.

### Verifying

Smoke-test from Claude Code:

```
agent_run(task="List the open issues in my repo using mcp__gh__list_issues.")
```

After it finishes, read the transcript:

```bash
cat $(jq -r .out_dir <last_result>)/transcript.jsonl | grep '"mcp__gh__'
```

You should see `tool_call` records with `tool: "mcp__gh__list_issues"` and
no raw `MCP_GITHUB_TOKEN` value anywhere in the file (it should be
redacted). If the tool wasn't found, check the system prompt section of
the transcript — the "Additional MCP tools available" block lists exactly
what the worker discovered.

### Per-call override

For a single run with a different set of MCP servers:

```python
agent_run(
    task="...",
    mcp_config_json='{"mcpServers": {"pg": {"command": "uvx", "args": ["mcp-server-postgres", "postgres://..."]}}}'
)
```

The per-call config replaces the default for that run; it does not merge.

---

## 8. Common server recipes

### Filesystem (read-only over a directory)

```json
"fs": {
  "command": "uvx",
  "args": ["mcp-server-filesystem", "/srv/data"]
}
```

### GitHub

```json
"gh": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${MCP_GITHUB_TOKEN}"}
}
```

Note the exact env var name varies by server version — check the
upstream README.

### Slack

```json
"slack": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-slack"],
  "env": {
    "SLACK_BOT_TOKEN":    "${MCP_SLACK_BOT_TOKEN}",
    "SLACK_TEAM_ID":      "${MCP_SLACK_TEAM_ID}"
  }
}
```

### Postgres (read-only)

```json
"pg": {
  "command": "uvx",
  "args": ["mcp-server-postgres", "${MCP_PG_DSN}"]
}
```

Avoid putting the DSN in `args` if it carries credentials — `ps` shows
argv. Prefer an env var:

```json
"pg": {
  "command": "uvx",
  "args": ["mcp-server-postgres"],
  "env": {"DATABASE_URL": "${MCP_PG_DSN}"}
}
```

### Remote SSE / HTTP MCP

```json
"internal": {
  "transport": "sse",
  "url": "https://mcp.example.internal/sse",
  "headers": {"Authorization": "Bearer ${MCP_INTERNAL_KEY}"}
}
```

### Custom Python MCP from a repo

```json
"my-tool": {
  "command": "uvx",
  "args": ["--from", "git+https://github.com/you/your-mcp-server.git", "your-mcp-cli"]
}
```

`uvx` pulls and caches at first use; subsequent runs are fast.

---

## 9. Observability

Each run's output directory contains:

- `transcript.jsonl` — every model message and every tool call, with
  arguments and results. Secrets in `env` / `headers` values are
  redacted. Look for entries with `"tool": "mcp__..."` to see exactly
  what the model invoked.
- `tool_outputs/<n>-mcp__server__tool.json` — full output of any MCP
  call whose result exceeded the inline cap (4 KB). The transcript
  records a short head plus a `stored_at` pointer to this file.
- `summary.md` — worker's `finish()` summary.

To audit a run end-to-end:

```bash
# What MCP tools did the model actually call?
jq -r 'select(.kind=="tool_call") | .tool' transcript.jsonl | sort -u

# Did any MCP call error?
jq -c 'select(.kind=="tool_call" and .tool|startswith("mcp__"))
       | select(.result.is_error==true or .result.error)' transcript.jsonl
```

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `unknown MCP transport 'xxx'` | Typo in `transport` field. | Use `stdio`, `sse`, or `http`. |
| `mcp call failed: ...` returned to model | Upstream server raised. | Check the server's own stderr — for stdio servers the worker forwards stderr to the container log (`docker logs vllm-agent`). |
| Tool not visible to the model | (a) `enabled_tools` excludes it, or (b) the server didn't expose it during `list_tools()`. | Remove `enabled_tools` or verify the upstream tool name. |
| `${VAR}` shows up literally inside the server | Var wasn't forwarded by env_overlay. | Rename to `MCP_*` so the front MCP forwards it, or add it to `_ENV_OVERLAY_ALLOWLIST` in `vllm_mcp.py`. |
| Token appears in `transcript.jsonl` | Token was passed in `args`, not `env`/`headers`. | Move it into an `env` block (stdio) or `headers` (sse/http). |
| Worker hangs on connect | Stdio subprocess never sent the initialize response. | Verify the server actually runs by invoking the same command locally. Check container PATH includes `uvx` / `npx`. |
| `MCP server 'foo' not connected` error during execute | Registry was torn down before the tool fired (shouldn't happen — file a bug). | — |

---

## 11. Security model

| Concern | Behavior |
|---------|----------|
| Where do MCP subprocesses run? | Same trust boundary as the `bash` tool. In `mode="remote"`, that's the VM container; in `mode="local"`, your machine. |
| Are secrets logged? | `env` and `headers` values (literal **or** `${VAR}`-expanded) are added to the transcript redact list before the first MCP call fires. Token-shaped strings in those values are scrubbed wherever they appear in `transcript.jsonl`. |
| Are secrets in `args`? | Never — the worker doesn't add them. If **you** put a secret in `args`, it will show in `ps -ef` and in tool-call records. Don't. |
| Can the model call arbitrary servers? | Only the ones in your config. The model can't add servers at runtime. |
| Can a server escape the sandbox? | A stdio subprocess inherits the container's filesystem and network (and `gosu agent` user privileges). Treat each MCP server as code-equivalent to bash. |
| What if the front MCP env contains other secrets? | Only `_ENV_OVERLAY_ALLOWLIST` entries and `MCP_*`-prefixed vars are forwarded. Everything else stays on your host. |

---

## 12. Reference

Code paths:
- `vllm-agent/src/vllm_agent/tools/mcp_client.py` — `MCPRegistry`, `load_mcp_config`, `_expand`, `_expand_dict`.
- `vllm-agent/src/vllm_agent/api.py` — wiring into `agent_run` / `agent_session_step` lifecycle.
- `vllm-agent/src/vllm_agent/prompts.py` — `_format_mcp_block` (system-prompt injection).
- `vllm-agent/src/vllm_agent/server.py` — HTTP request body schema.
- `mcp-server/vllm_mcp.py` — front MCP: `_resolve_mcp_config`, `_build_env_overlay`, `_ENV_OVERLAY_PREFIXES`, `agent_run` / `agent_session_start` tool signatures.
- `compose/vllm-agent/Dockerfile` — container runtime: ships `uv` / `uvx` (Astral) and Node 24 / `npx` (NodeSource).

Tests:
- `vllm-agent/tests/test_mcp_client.py`
- `vllm-agent/tests/test_prompts_mcp_block.py`
