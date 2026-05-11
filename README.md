# rtx-inference

Self-hosted Qwen3-Coder-30B vLLM + agent runtime, accessible from Claude Code via MCP.

## Quick Start

### 1. Clone

```bash
git clone https://github.com/vantagecompute/rtx_5090_dev
cd rtx_5090_dev
```

### 2. Install the MCP server

```bash
cd mcp-server && python -m venv .venv && .venv/bin/pip install -e . && cd ..
```

### 3. Configure MCP

Add the `vllm-rtx5090` server to your Claude Code user settings at `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "vllm-rtx5090": {
      "command": "/absolute/path/to/rtx_5090_dev/mcp-server/.venv/bin/python",
      "args": [
        "/absolute/path/to/rtx_5090_dev/mcp-server/vllm_mcp.py"
      ],
      "env": {
        "VLLM_BASE_URL": "https://your-endpoint.ngrok-free.dev",
        "VLLM_MODEL": "QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ",
        "VLLM_API_KEY": "your-vllm-api-key",
        "VLLM_AGENT_URL": "https://your-endpoint.ngrok-free.dev/agent",
        "VLLM_AGENT_API_KEY": "your-agent-api-key",
        "GITHUB_TOKEN": "ghp_...",
        "GIT_AUTHOR_NAME": "Your Name",
        "GIT_AUTHOR_EMAIL": "you@example.com"
      }
    }
  }
}
```

| Key | Required | Description |
|-----|----------|-------------|
| `VLLM_BASE_URL` | yes | ngrok URL or direct VM address (e.g. `http://VM:8443`) |
| `VLLM_MODEL` | yes | Model name as reported by `/v1/models` |
| `VLLM_API_KEY` | yes | Bearer token for the vLLM endpoint |
| `VLLM_AGENT_URL` | yes | Agent base URL — typically `${VLLM_BASE_URL}/agent` |
| `VLLM_AGENT_API_KEY` | yes | Bearer token for the agent |
| `GITHUB_TOKEN` | no | PAT forwarded to remote worker for `git`/`gh` commands |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` | no | Used by `git commit` inside the VM |

### 4. Add the dispatch skill

Install [vllm-dispatch-skill](https://github.com/vantagecompute/vllm-dispatch-skill) to teach Claude Code when and how to offload work to the RTX-5090:

```bash
# follow install instructions in that repo
```

### 5. Test

Restart Claude Code to load the MCP server, then verify:

```
# Health check
"use the vllm-rtx5090 health tool to check if the endpoint is up"

# Quick dispatch
"use agent_run to write a hello world Python script"
```

## Docs

- [Architecture](docs/architecture.md) — components, URL layout, repo structure
- [Provisioning](docs/provisioning.md) — launching the LXD VM with GPU passthrough
- [MCP tools reference](docs/mcp-tools.md) — all tools, modes, output discipline, skills
