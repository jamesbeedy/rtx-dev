# rtx-inference

Self-hosted Qwen3-Coder-30B-A3B vLLM endpoint behind an MCP server, with a
companion agent runtime (`vllm-agent`) so Claude Code can offload bulk codegen
and iterative coding work to the local 5090.

## Components

The deployment is a docker compose stack running inside an LXD VM with GPU
passthrough. Three services:

- **vllm** — upstream `vllm/vllm-openai` image with the model + quant args
  passed via env-substituted `command:`. Bind-mounts an `hf-cache` named
  volume for model weights. GPU exposed via nvidia-container-toolkit.
- **vllm-agent** — `python:3.12-slim` running the `vllm-agent serve` HTTP
  API. Source bind-mounted from `/home/ubuntu/rtx_5090_dev/vllm-agent`;
  `pip install -e` runs at container start. Reaches vllm at
  `http://vllm:8000` over the compose internal network.
- **nginx** — `nginx:alpine` with path-prefix routing on `:8443`:
  `/v1/*` → vllm, `/agent/*` → vllm-agent. The only externally-reachable
  port on the VM.

Outside the VM:

- **mcp-server** — local MCP shim on the orchestrator's machine; talks to
  the VM via the unified `:8443` endpoint.

## Provisioning

```bash
./launch-inference.sh --lxd-host USER@LXD-CLUSTER-MEMBER
```

This:
1. Creates an LXD VM with GPU passthrough.
2. Cloud-init installs Docker CE + nvidia-container-toolkit + the NVIDIA
   open kernel module, then reboots.
3. Tar/scp/lxc-file-push the local repo into the VM at
   `/home/ubuntu/rtx_5090_dev`.
4. Generates `/home/ubuntu/rtx_5090_dev/.env` (mode 0600) from CLI args.
5. Runs `docker compose pull && docker compose up -d` as the ubuntu user.
6. Polls `/v1/models` and `/agent/skills` through nginx until both
   backends are responsive.
7. Updates `.mcp.json` with `VLLM_BASE_URL=http://<VM>:8443` and
   `VLLM_AGENT_URL=http://<VM>:8443/agent`.

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

## URL layout

The VM exposes a single port (`8443`) running nginx as a compose service.
Path prefixes route to the right backend:

| External URL                     | Backend                  |
|----------------------------------|--------------------------|
| `http://VM:8443/v1/...`          | vllm (OpenAI-compatible) |
| `http://VM:8443/health`          | vllm liveness probe      |
| `http://VM:8443/agent/run`       | vllm-agent /run          |
| `http://VM:8443/agent/session/*` | vllm-agent /session/*    |
| `http://VM:8443/agent/skills`    | vllm-agent /skills       |
| `http://VM:8443/agent/artifacts` | vllm-agent /artifacts    |

Authorization headers (Bearer tokens) are forwarded through nginx
unchanged. TLS termination is on the roadmap as a future plan.

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
