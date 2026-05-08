# Docker Compose Stack — Design

Date: 2026-05-08
Status: Approved (brainstorming phase)
Next step: writing-plans → implementation plan

---

## 1. Goal & Non-Goals

### Goal

Replace the systemd-based service stack inside the LXD VM with a 3-service
docker compose stack: `vllm` (upstream image), `vllm-agent` (our code,
bind-mounted), and `nginx` (path-prefix reverse proxy). One externally
reachable port (`8443`); GPU passthrough via nvidia-container-toolkit;
everything else lives in the compose internal network.

### Non-Goals

- TLS termination. Plain HTTP on `:8443`. TLS is a future plan.
- CI-built images / a registry. We bind-mount source for `vllm-agent`.
- Multi-host orchestration (swarm, k8s). One VM, one stack.
- Zero-downtime upgrades. `docker compose up -d` recreates affected services
  with brief downtime; acceptable for a single-user setup.
- Local-workstation parity. The 5090 is the only relevant GPU; running the
  same stack on a non-CUDA workstation is out of scope.
- Re-introducing nginx via systemd (Plan E) — superseded by this plan.

---

## 2. Architecture

```
                       LXD VM (Ubuntu 24.04, GPU passthrough)
                       ┌────────────────────────────────────────────────┐
                       │  docker compose                                │
                       │  ┌──────────────┐ ┌──────────────┐ ┌────────┐ │
              LAN  ───▶│  │   nginx      │ │  vllm-agent  │ │  vllm  │ │
              :8443    │  │  :8443       │ │  (python)    │ │ (CUDA) │ │
                       │  └────┬─────────┘ └────┬─────────┘ └───┬────┘ │
                       │       │ /v1/*  → vllm  │ HTTP to vllm  │      │
                       │       │ /agent/* →     │ via service   │ GPU  │
                       │       │   vllm-agent   │ name          │      │
                       │       └────────────────┴───────────────┘      │
                       │       compose internal network                 │
                       └────────────────────────────────────────────────┘
                       Volumes: hf-cache, vllm-agent-sessions,
                                vllm-agent-runs
```

- Externally reachable: only nginx on `:8443`.
- `vllm` and `vllm-agent` have no `ports:` mapping — internal-only.
- `vllm-agent` reaches `vllm` via service-name DNS (`http://vllm:8000`).
- Three named volumes persist HF model cache, vllm-agent sessions, and
  agent run outputs across container restarts.

---

## 3. Compose services

`compose.yaml` lives at the repo root. Three services.

### `vllm`

- **Image:** `vllm/vllm-openai:latest`
- **GPU:** via nvidia-container-toolkit (`gpus: all` or
  `runtime: nvidia` + `NVIDIA_VISIBLE_DEVICES=all` per the toolkit's docs)
- **Volumes:** `hf-cache:/root/.cache/huggingface`
- **Ports:** none — internal only
- **Command:** `--model ${VLLM_MODEL} --max-model-len ${VLLM_MAX_LEN}
  --gpu-memory-utilization ${VLLM_GPU_UTIL} --port 8000 --host 0.0.0.0
  --quantization ${VLLM_QUANT_ARGS}` (env-substituted from `.env`)
- **Environment:** `VLLM_API_KEY=${VLLM_API_KEY}` (forwarded to vLLM if set)
- **Restart:** `unless-stopped`
- **Healthcheck:** `CMD curl -f http://localhost:8000/v1/models` so other
  services can `depends_on: { vllm: { condition: service_healthy } }`

### `vllm-agent`

- **Image:** `python:3.12-slim`
- **User:** `"1000:1000"` (the host's `ubuntu` user) — so files written into
  bind mounts (e.g. `.egg-info` from `pip install -e`) appear as ubuntu on
  the host, not root.
- **Volumes:**
  - `/home/ubuntu/rtx_5090_dev/vllm-agent:/app` — source tree, read-write.
    Read-write (not `:ro`) because `pip install -e .` writes `.egg-info`.
  - `/home/ubuntu/.claude:/skills:ro` — user's skill roots for `list_skills`
  - `vllm-agent-sessions:/var/lib/vllm-agent/sessions`
  - `vllm-agent-runs:/home/agent/.cache/vllm-agent/runs`
- **Ports:** none
- **Command:** `bash -c "pip install --quiet --user -e /app && vllm-agent serve
  --host 0.0.0.0 --port 8088"`. The `--user` flag installs into
  `~/.local` (no system-site-packages writes; works fine as uid 1000).
- **Environment:**
  - `VLLM_BASE_URL=http://vllm:8000` (service-name DNS)
  - `VLLM_MODEL=${VLLM_MODEL}`
  - `VLLM_API_KEY=${VLLM_API_KEY}` (for vllm-agent → vllm calls if vLLM auth on)
  - `VLLM_AGENT_API_KEY=${VLLM_AGENT_API_KEY}`
  - `DDG_MIN_INTERVAL_S=${DDG_MIN_INTERVAL_S}`
  - `VLLM_AGENT_SESSION_ROOT=/var/lib/vllm-agent/sessions`
- **`depends_on:`** `vllm: { condition: service_healthy }`
- **Restart:** `unless-stopped`

### `nginx`

- **Image:** `nginx:alpine` (~10 MB)
- **Volumes:** `./compose/nginx.conf:/etc/nginx/conf.d/default.conf:ro`
- **Ports:** `"8443:8443"` — only externally-reachable port
- **`depends_on:`** `[vllm-agent]`
- **Restart:** `unless-stopped`

### Repo additions

```
rtx_5090_dev/
├── compose.yaml                    # NEW
├── compose/
│   └── nginx.conf                  # NEW
├── .env.example                    # NEW (committed; documents the contract)
├── .gitignore                      # MODIFY (+ .env)
└── ... (existing layout unchanged)
```

---

## 4. nginx config + auth flow

### `compose/nginx.conf`

```nginx
server {
    listen 8443;
    server_name _;

    proxy_read_timeout 1800s;
    proxy_send_timeout 60s;
    proxy_connect_timeout 10s;
    client_max_body_size 16m;

    # vLLM (OpenAI-compatible API)
    location /v1/ {
        proxy_pass http://vllm:8000;
        proxy_http_version 1.1;
        proxy_buffering off;          # streaming chat-completions
    }

    # vLLM liveness probe
    location = /health {
        proxy_pass http://vllm:8000/health;
    }

    # vllm-agent (path prefix stripped by trailing slash)
    location /agent/ {
        proxy_pass http://vllm-agent:8088/;
        proxy_http_version 1.1;
        proxy_buffering off;
    }
}
```

Differences from the Plan E config: backends use compose service-name DNS
(`vllm`, `vllm-agent`) instead of `127.0.0.1`. nginx in the compose network
resolves these names automatically.

### Auth flow (Plan D's API key — unchanged behavior)

`vllm-agent`'s server-side `require_key` dependency is unchanged. The
`Authorization: Bearer <key>` header passes through nginx by default
(nginx forwards client headers via `proxy_pass` without modification).
End-user-visible behavior:

```
curl http://VM:8443/agent/run                                    → 401
curl -H "Authorization: Bearer $KEY" http://VM:8443/agent/run …  → 200
curl http://VM:8443/v1/models                                    → 200
curl http://VM:8443/health                                       → 200
```

The MCP shim's `_agent_headers()` reads `VLLM_AGENT_API_KEY` from env and
threads `Authorization: Bearer <key>` on every `/agent/*` call.

### vLLM's own `--api-key` (separate from the agent key)

The launch script's existing `--api-key` flag (Bearer auth on `/v1/*`)
still works — nginx forwards the header. The CLI arg threading into
`.env` is preserved.

---

## 5. Secrets + env handling

### The `.env` file (compose's standard pattern)

Compose auto-reads a file named `.env` next to `compose.yaml` and
substitutes `${VAR}` references in the compose file.

**`.env.example`** (committed, documents the contract):

```bash
# Required
VLLM_MODEL=QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ

# vLLM serve flags after --quantization
VLLM_QUANT_ARGS=awq_marlin --enable-auto-tool-choice --tool-call-parser qwen3_coder

# Optional auth keys (empty = disabled)
VLLM_API_KEY=
VLLM_AGENT_API_KEY=

# Tuning
VLLM_MAX_LEN=32768
VLLM_GPU_UTIL=0.92
DDG_MIN_INTERVAL_S=1.5
```

**`.env`** (NOT committed; `.gitignore`'d; lives only on the VM at
`/home/ubuntu/rtx_5090_dev/.env`, mode 0600). The launch script generates
this from CLI args.

### Compose-side substitution

In `compose.yaml`:

```yaml
services:
  vllm:
    command: >
      --model ${VLLM_MODEL}
      --max-model-len ${VLLM_MAX_LEN}
      --gpu-memory-utilization ${VLLM_GPU_UTIL}
      --port 8000 --host 0.0.0.0
      --quantization ${VLLM_QUANT_ARGS}
    environment:
      - VLLM_API_KEY=${VLLM_API_KEY}

  vllm-agent:
    environment:
      - VLLM_BASE_URL=http://vllm:8000
      - VLLM_MODEL=${VLLM_MODEL}
      - VLLM_API_KEY=${VLLM_API_KEY}
      - VLLM_AGENT_API_KEY=${VLLM_AGENT_API_KEY}
      - DDG_MIN_INTERVAL_S=${DDG_MIN_INTERVAL_S}
      - VLLM_AGENT_SESSION_ROOT=/var/lib/vllm-agent/sessions
```

### Security model

- `compose.yaml` is committed (no secrets in it; only `${VAR}` references).
- `.env` is generated on the VM by the launch script from CLI args; never
  enters the repo.
- This mirrors the existing model where cloud-init renders systemd
  `Environment=` lines from sed substitutions.

---

## 6. Launch script + cloud-init flow

### Cloud-init becomes simpler

`profiles/rtx-inference.yaml.tpl` shrinks: ~80 lines of vLLM venv install,
vllm-agent venv install, and three systemd unit definitions become ~25
lines that install Docker + nvidia-container-toolkit and reboot.

```yaml
runcmd:
  # 1. Install Docker CE
  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  - chmod a+r /etc/apt/keyrings/docker.gpg
  - |
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list
  - apt-get update -qq
  - DEBIAN_FRONTEND=noninteractive apt-get install -y -qq
      docker-ce docker-ce-cli containerd.io
      docker-buildx-plugin docker-compose-plugin

  # 2. NVIDIA stack
  - DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nvidia-open
  - curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey
      | gpg --dearmor -o /etc/apt/keyrings/nvidia-container-toolkit.gpg
  - |
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit.gpg] https://#' \
      > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  - apt-get update -qq
  - DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nvidia-container-toolkit
  - nvidia-ctk runtime configure --runtime=docker
  - systemctl restart docker

  # 3. Let ubuntu user invoke docker
  - usermod -aG docker ubuntu

power_state:
  mode: reboot
  delay: now
  condition: True
  message: Rebooting to load NVIDIA open kernel modules
```

Gone: vLLM venv, vllm-agent venv, `vllm.service`, `vllm-agent.service`,
the lxc-file-push install dance.

### Launch script flow

1. Tar local repo → scp to LXD host → `lxc file push` into VM (unchanged).
2. Extract under `/home/ubuntu/rtx_5090_dev` (unchanged).
3. **NEW:** Generate `/home/ubuntu/rtx_5090_dev/.env` from CLI args, mode 0600.
4. **NEW:** `lxc exec ... su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose pull && docker compose up -d'`.
5. Probes: poll `127.0.0.1:8443/v1/models` for vLLM (now via nginx) and
   `127.0.0.1:8443/agent/skills` (or similar) for vllm-agent.
6. `.mcp.json` updater writes the unified URL: `VLLM_BASE_URL=http://VM:8443`,
   `VLLM_AGENT_URL=http://VM:8443/agent`.

### `.env` lifecycle

- **First provision:** launch script writes `.env` from CLI args.
- **Re-provision:** wipe + recreate VM, fresh `.env` written.
- **Iteration on a running VM** (no reprovision): edit `.env` on the VM,
  `docker compose up -d` to apply. Out of scope for the launch script —
  YAGNI.

---

## 7. Testing + rollout

### Offline checks (no VM)

- `docker compose config` against a test `.env` — validates schema +
  substitution.
- `docker run --rm -v $(pwd)/compose/nginx.conf:/etc/nginx/conf.d/default.conf:ro
  nginx:alpine nginx -t` — validates nginx config without a running stack.
- YAML lint of `profiles/rtx-inference.yaml.tpl` (existing pattern).
- `bash -n launch-inference.sh` (existing pattern).

### Live checks (after VM provision)

- `nvidia-smi` inside the `vllm` container — confirms GPU passthrough.
- vLLM `/v1/models` reachable through nginx.
- `vllm-agent /agent/skills` reachable through nginx.
- `vllm-agent` resolves `vllm` via service-name DNS.
- `agent_run` end-to-end via the unified port.
- API key gate works on `/agent/*` (401 without, 200 with).

### Rollout order

1. Land repo files: `compose.yaml`, `compose/nginx.conf`, `.env.example`,
   `.gitignore` update. Validate offline. **No deployment.**
2. Refactor `profiles/rtx-inference.yaml.tpl`. Validate YAML.
3. Refactor `launch-inference.sh` (`.env` gen + `docker compose up -d`).
   Validate bash.
4. Reprovision dogfood — full launch against the live LXD host. Smoke-test
   the auth-gated path through nginx.
5. Update README + CLAUDE.md.

### Out of scope (intentional)

- TLS termination (Plan G).
- Multi-VM scaling, swarm mode, k8s.
- Pre-built images via CI/registry.
- Local-workstation parity.
- Zero-downtime upgrades.
- Healthcheck endpoint for nginx itself.

---

## Constraints & Risks

- **nvidia-container-toolkit + Blackwell consumer GPUs (SM120/121):** the
  toolkit must be a recent enough version. If the VM image's `nvidia-open`
  + nvidia-container-toolkit combination doesn't support SM120, the `vllm`
  container's `nvidia-smi` will fail. Mitigation: pin to a known-working
  toolkit version if needed.
- **First model load is still ~10–20 min** (HF download + warmup), same as
  the systemd path. Not a regression.
- **vllm-agent install on first start** runs `pip install --user -e /app`
  inside the container; takes ~15-30s. The container's restart policy will
  cause this to re-run on every restart — acceptable. The `--user` flag
  + `user: "1000:1000"` ensures pip writes only to `~/.local` (no root
  perms needed). The bind-mounted source gets a `.egg-info` dir owned by
  ubuntu on the host. If this becomes annoying, bake a `.venv` into a
  custom image (out of scope).
- **Bind-mount path is host-absolute.** `/home/ubuntu/rtx_5090_dev` must
  exist with the correct contents before `docker compose up`. The launch
  script's lxc-file-push step ensures this.
- **`.env` not committed** is load-bearing. If anyone accidentally commits
  it, secrets leak to git. The `.gitignore` entry is the only enforcement
  — no pre-commit hook (YAGNI).
- **No backup of named volumes** — `hf-cache` and the agent volumes are
  ephemeral relative to VM lifetime. Re-provision = re-download model.
  Could mount HF cache from LXD host as a future optimization (deferred).
