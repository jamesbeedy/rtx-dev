# Worker git tools + GitHub PAT pass-through

**Date:** 2026-05-11
**Status:** Draft

## Goal

Let the remote `vllm-agent` worker run `git` and `gh` CLI commands authenticated
as the user, without redeploying the VM or restarting containers when the token
rotates. Token and identity values originate in the user's `.mcp.json` env on
the orchestrator side and flow per-request through the MCP server to the worker.

## Non-goals

- No first-class git tool wrappers. Worker uses the existing `bash` tool — model
  already knows `git`/`gh` syntax.
- No general environment passthrough. Strict three-key allowlist.
- No automatic `gh auth setup-git` at worker startup. The model runs it
  explicitly when it needs `git push` over HTTPS.

## Architecture

### 1. Container — install CLIs

`compose/vllm-agent/Dockerfile`: add `git` and `gh` to the apt install layer.
`gh` package is in the `cli/cli` PPA / GitHub APT repo; follow the standard
GitHub CLI install snippet so the image build is reproducible.

### 2. MCP server — allowlist + forward

`mcp-server/vllm_mcp.py`:

- Read three optional env vars at module load:
  - `GITHUB_TOKEN`
  - `GIT_AUTHOR_NAME`
  - `GIT_AUTHOR_EMAIL`
- Build `_ENV_OVERLAY: dict[str, str]` from whichever are set (skip unset/empty).
- In `_agent_run_remote` and `_http_session_start`, include
  `"env_overlay": _ENV_OVERLAY` in the POST body when non-empty.
- Same dict also passed to local-mode `agent_run` / `agent_session_start` via
  the existing `_AgentRunRequest` / `_AgentSessionStartRequest` dataclasses.

### 3. vllm-agent API surface

`vllm-agent/src/vllm_agent/server.py`:

- `RunBody.env_overlay: dict[str, str] | None = None`
- `SessionStartBody.env_overlay: dict[str, str] | None = None`

`vllm-agent/src/vllm_agent/api.py`:

- `AgentRunRequest.env_overlay: dict[str, str] | None = None`
- `AgentSessionStartRequest.env_overlay: dict[str, str] | None = None`

Plumb through `agent_run` / `agent_session_start` into `ToolContext`.

### 4. ToolContext + bash tool

`vllm-agent/src/vllm_agent/tools/base.py`:

- `ToolContext.env_overlay: dict[str, str]` (default `{}`).

`vllm-agent/src/vllm_agent/tools/shell.py` (`_bash`):

- Build subprocess env: `env = {**os.environ, **ctx.env_overlay}`.
- Pass `env=env` to `asyncio.create_subprocess_shell`.
- Worker subshell now sees `GITHUB_TOKEN` (which `gh` auto-detects as auth) and
  `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` (which `git commit` reads if no local
  config is set).

### 5. Transcript redaction

`vllm-agent/src/vllm_agent/transcript.py`:

- `Transcript.__init__` accepts `redact_values: list[str]` (defaults to empty).
- Before writing each JSONL line, replace every occurrence of each redact value
  with `[REDACTED]`. Substring-based on the serialized JSON string.
- Loop wires `redact_values=list(ctx.env_overlay.values())` when constructing
  the transcript writer. Empty/short values (e.g. < 8 chars) are skipped to
  avoid mangling unrelated text.

### 6. `.mcp.json` config

Add to the existing `env` block:

```json
"GITHUB_TOKEN": "ghp_...",
"GIT_AUTHOR_NAME": "James Beedy",
"GIT_AUTHOR_EMAIL": "james@vantagecompute.ai"
```

`.mcp.json` is gitignored already.

## Data flow

```
.mcp.json env
    │
    ▼
MCP server (os.environ at startup → _ENV_OVERLAY)
    │
    ▼ POST /run  body.env_overlay
vllm-agent HTTP server (RunBody.env_overlay)
    │
    ▼
AgentRunRequest.env_overlay → ToolContext.env_overlay
    │
    ▼
bash tool → subprocess env = os.environ | ctx.env_overlay
    │
    ▼
git / gh commands authenticated
```

## Security

- **Allowlist** on MCP side prevents accidental leak of other env vars from the
  user's shell into request bodies.
- **Transcript redaction** prevents token leak when the model echoes the token
  in `bash` command strings or in stdout that the bash tool captures.
- **No echo-back** in API responses: `agent_run` result already omits env;
  ensure new field is not added to response shape.
- **Container scope**: env overlay applies only to the worker subprocess. Other
  services in the compose stack (vllm, nginx, ngrok) do not see it.

## Failure modes

- `GITHUB_TOKEN` unset in `.mcp.json` → overlay omits the key; worker bash sees
  no token; `gh` prompts for auth → fails non-interactively. Acceptable: model
  reports failure, user adds key.
- Token expired → `gh`/`git` return auth error in stdout; model surfaces it.
- `gh` not installed in container → model gets `command not found`; surface as
  build regression. Mitigated by Dockerfile change in step 1.

## Out of scope / future

- Per-task scoped tokens (short-lived).
- Pushing the overlay through session resume (overlay is captured at session
  start; rotating mid-session requires a new session).
- A structured `git`/`gh` tool wrapper. Add only if model error rate on bash
  invocations becomes a problem.
