# Architecture

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

## URL Layout

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

Authorization headers (Bearer tokens) are forwarded through nginx unchanged.
TLS termination is on the roadmap as a future plan.

## Repository Layout

```
rtx_5090_dev/
├── vllm-agent/                         # standalone runtime package
├── mcp-server/                         # MCP stdio shim
├── profiles/rtx-inference.yaml.tpl     # cloud-init for the LXD VM
├── launch-inference.sh                 # provisioning script
├── docs/                               # reference docs
├── .mcp.json                           # MCP server registration
└── README.md
```
