# Plan F: Docker Compose Stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the systemd-based 3-unit deployment (vllm + vllm-agent + Plan-E nginx) inside the LXD VM with a docker compose stack: `vllm` (upstream image), `vllm-agent` (our code, bind-mounted), `nginx` (path-prefix reverse proxy). Single externally-reachable port `:8443`; GPU passthrough via nvidia-container-toolkit. Plan E (systemd nginx) is superseded — its nginx config carries forward.

**Architecture:** A `compose.yaml` at the repo root + a `compose/nginx.conf` describe the runtime topology. Cloud-init in the VM installs Docker CE + nvidia-container-toolkit and reboots; that's all. The launch script tars the local repo, scp's it to the LXD host, `lxc file push`'s it into the VM, generates `/home/ubuntu/rtx_5090_dev/.env` from CLI args, and runs `docker compose up -d` as the ubuntu user. No more vLLM venv, no more vllm-agent venv, no more three systemd units.

**Tech Stack:** Docker CE, docker-compose-plugin (v2), nvidia-container-toolkit, nginx:alpine, vllm/vllm-openai upstream image, python:3.12-slim. No Python/Bash code changes inside `vllm-agent` or `mcp-server` — only env-var values shift in `.env` / `.mcp.json`.

**Source spec:** `docs/superpowers/specs/2026-05-08-docker-compose-stack-design.md`

---

## File Structure

```
rtx_5090_dev/
├── compose.yaml                            # NEW — service definitions
├── compose/
│   └── nginx.conf                          # NEW — Plan-E config, service-name DNS
├── .env.example                            # NEW — committed; documents env contract
├── .gitignore                              # MODIFY — add .env
├── profiles/
│   └── rtx-inference.yaml.tpl              # REWRITE — Docker + ncc-toolkit only
├── launch-inference.sh                     # MODIFY — .env gen + docker compose up
├── README.md                               # MODIFY — document compose architecture
└── CLAUDE.md                               # MODIFY (small) — note compose deploy
```

7 files, 7 tasks. Tasks 1-2 are repo-only (no deploy). Tasks 3-5 must land together for a working deploy. Task 6 is the live dogfood. Task 7 is docs.

**Migration note:** This plan is a one-way refactor. After landing, the next `./launch-inference.sh` reprovision will use compose, not systemd. Pre-Plan-F VMs running the old systemd units need to be reprovisioned to pick up the compose stack — there is no in-place migration path. State persists across reprovisions only via what fits in named docker volumes (which themselves are wiped during VM reprovision); model weights re-download.

---

## Task 1: Add `compose.yaml` and `compose/nginx.conf`

**Files:**
- Create: `compose.yaml`
- Create: `compose/nginx.conf`

- [ ] **Step 1: Write `compose.yaml`**

```yaml
# Single-host docker compose stack inside the rtx5090 LXD VM.
# Three services: vllm (upstream image), vllm-agent (our code, bind-mounted),
# nginx (path-prefix reverse proxy on :8443).
# See docs/superpowers/specs/2026-05-08-docker-compose-stack-design.md.

services:
  vllm:
    image: vllm/vllm-openai:latest
    restart: unless-stopped
    gpus: all
    volumes:
      - hf-cache:/root/.cache/huggingface
    environment:
      - VLLM_API_KEY=${VLLM_API_KEY}
    command: >
      --model ${VLLM_MODEL}
      --max-model-len ${VLLM_MAX_LEN}
      --gpu-memory-utilization ${VLLM_GPU_UTIL}
      --port 8000 --host 0.0.0.0
      --quantization ${VLLM_QUANT_ARGS}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/v1/models"]
      interval: 30s
      timeout: 5s
      retries: 30
      start_period: 600s

  vllm-agent:
    image: python:3.12-slim
    restart: unless-stopped
    user: "1000:1000"
    depends_on:
      vllm:
        condition: service_healthy
    volumes:
      - /home/ubuntu/rtx_5090_dev/vllm-agent:/app
      - /home/ubuntu/.claude:/skills:ro
      - vllm-agent-sessions:/var/lib/vllm-agent/sessions
      - vllm-agent-runs:/home/agent/.cache/vllm-agent/runs
    environment:
      - HOME=/home/agent
      - VLLM_BASE_URL=http://vllm:8000
      - VLLM_MODEL=${VLLM_MODEL}
      - VLLM_API_KEY=${VLLM_API_KEY}
      - VLLM_AGENT_API_KEY=${VLLM_AGENT_API_KEY}
      - DDG_MIN_INTERVAL_S=${DDG_MIN_INTERVAL_S}
      - VLLM_AGENT_SESSION_ROOT=/var/lib/vllm-agent/sessions
      - PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin
    command: >
      bash -c "
      mkdir -p /home/agent/.cache/vllm-agent/runs &&
      pip install --quiet --user --no-warn-script-location -e /app &&
      exec vllm-agent serve --host 0.0.0.0 --port 8088
      "

  nginx:
    image: nginx:alpine
    restart: unless-stopped
    depends_on:
      - vllm-agent
    volumes:
      - ./compose/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    ports:
      - "8443:8443"

volumes:
  hf-cache:
  vllm-agent-sessions:
  vllm-agent-runs:
```

- [ ] **Step 2: Write `compose/nginx.conf`**

```bash
mkdir -p /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/compose
```

Then create `compose/nginx.conf`:

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
        proxy_buffering off;
    }

    # vLLM liveness probe (mcp-server's health() tool calls /health)
    location = /health {
        proxy_pass http://vllm:8000/health;
    }

    # vllm-agent (path prefix stripped by trailing slash on proxy_pass)
    location /agent/ {
        proxy_pass http://vllm-agent:8088/;
        proxy_http_version 1.1;
        proxy_buffering off;
    }
}
```

- [ ] **Step 3: Validate `compose.yaml` syntax + env substitution**

Create a temporary `.env` with placeholder values and run `docker compose config`:

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
cat > .env <<'EOF'
VLLM_MODEL=test-model
VLLM_QUANT_ARGS=fp8
VLLM_MAX_LEN=32768
VLLM_GPU_UTIL=0.92
VLLM_API_KEY=
VLLM_AGENT_API_KEY=
DDG_MIN_INTERVAL_S=1.5
EOF
docker compose config >/dev/null && echo "compose OK"
rm .env
```

Expected output: `compose OK`. If `docker compose` is not installed locally, install it via `sudo apt-get install -y docker-compose-plugin` first OR skip this check and rely on the live-VM validation in Task 6.

- [ ] **Step 4: Validate `nginx.conf` syntax**

```bash
docker run --rm -v /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/compose/nginx.conf:/etc/nginx/conf.d/default.conf:ro nginx:alpine nginx -t 2>&1 | tail -2
```

Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`. (Will fail offline if `nginx:alpine` isn't pulled; if so skip and rely on Task 6.)

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add compose.yaml compose/nginx.conf
git commit -m "Plan F: add compose.yaml + nginx config (3-service stack)"
```

---

## Task 2: Add `.env.example` and gitignore the real `.env`

**Files:**
- Create: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Write `.env.example`**

`/home/bdx/allcode/github/vantagecompute/rtx_5090_dev/.env.example`:

```bash
# Copy to `.env` (which is gitignored) and fill in values.
# This file is read by `docker compose` for ${VAR} substitution in compose.yaml.

# Required: model name passed to vLLM
VLLM_MODEL=QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ

# vLLM serve flags after --quantization (smuggled-in flags pattern from launch-inference.sh)
VLLM_QUANT_ARGS=awq_marlin --enable-auto-tool-choice --tool-call-parser qwen3_coder

# Optional: vLLM API key (Bearer auth on /v1/*); empty = no auth
VLLM_API_KEY=

# Optional: vllm-agent API key (Bearer auth on /agent/*); empty = no auth
VLLM_AGENT_API_KEY=

# Tuning
VLLM_MAX_LEN=32768
VLLM_GPU_UTIL=0.92
DDG_MIN_INTERVAL_S=1.5
```

- [ ] **Step 2: Update `.gitignore`**

Append to `/home/bdx/allcode/github/vantagecompute/rtx_5090_dev/.gitignore`:

```
# docker compose secrets — generated on the VM by launch-inference.sh
.env
```

(If `.gitignore` doesn't exist yet, create it with these two lines.)

- [ ] **Step 3: Verify `.env` would be ignored**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
touch .env
git check-ignore -v .env && echo "ignored OK"
rm .env
```

Expected: prints the gitignore source line + `ignored OK`. If it shows nothing, `.env` is NOT being ignored — fix the path/pattern in step 2.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add .env.example .gitignore
git commit -m "Plan F: .env.example + gitignore real .env"
```

---

## Task 3: Rewrite `profiles/rtx-inference.yaml.tpl` for Docker + nvidia-container-toolkit

**Files:**
- Modify: `profiles/rtx-inference.yaml.tpl`

The current template installs vLLM as a Python venv, defines `vllm.service` and `vllm-agent.service` systemd units, and threads model/quant/key args via sed-substituted `__TOKENS__` directly into systemd `ExecStart=` and `Environment=` lines. Plan F replaces all of that with: install Docker, install nvidia-container-toolkit, reboot. Service-shaped concerns move to `compose.yaml`.

The placeholders that disappear from the template:
- `__VLLM_MODEL__`, `__VLLM_MAX_LEN__`, `__VLLM_GPU_UTIL__`, `__VLLM_QUANT__` (these now flow through `.env`)
- `__VLLM_API_KEY_ARG__`, `__VLLM_AGENT_API_KEY__`, `__DDG_MIN_INTERVAL__` (same)

The placeholders that remain: `__PROFILE_NAME__`, `__BRIDGE__`, `__STORAGE_POOL__`, `__ROOT_SIZE__`, `__LIMITS_CPU__`, `__LIMITS_MEMORY__` — these are LXD-profile-shaped, not service-shaped.

- [ ] **Step 1: Read the current template once for context**

```bash
wc -l /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/profiles/rtx-inference.yaml.tpl
head -30 /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/profiles/rtx-inference.yaml.tpl
```

Identify the `[Unit]`/`[Service]` blocks for `vllm.service` and `vllm-agent.service`, plus the `runcmd` step that creates `/opt/vllm` venv. These all get removed.

- [ ] **Step 2: Replace the file**

Overwrite `profiles/rtx-inference.yaml.tpl` entirely with this content:

```yaml
config:
  limits.cpu: "__LIMITS_CPU__"
  limits.memory: __LIMITS_MEMORY__
  user.user-data: |
    #cloud-config
    package_update: true
    package_upgrade: false
    packages:
      - ca-certificates
      - curl
      - gnupg
      - jq
      - python3-venv
    runcmd:
      # ---- 1. Docker CE from the official repo --------------------------
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

      # ---- 2. NVIDIA open kernel module + container toolkit ------------
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

      # ---- 3. Allow ubuntu user to invoke docker (no sudo) -------------
      - usermod -aG docker ubuntu

    power_state:
      mode: reboot
      delay: now
      condition: True
      message: Rebooting to load NVIDIA open kernel modules

description: GPU passthrough profile for the rtx5090 LXD VM
devices:
  eth0:
    name: eth0
    nictype: bridged
    parent: __BRIDGE__
    type: nic
  root:
    path: /
    pool: __STORAGE_POOL__
    size: __ROOT_SIZE__
    type: disk
name: __PROFILE_NAME__
```

(If the existing template has additional fields under `devices:` or `config:` that aren't shown above, preserve them. The above is the structural skeleton; review the existing file once for any project-specific bits to carry over — e.g. extra apt repos or `lxc.apparmor.profile` overrides — and merge them in.)

- [ ] **Step 3: Validate YAML parses with placeholders substituted**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev && python3 -c "
import yaml
with open('profiles/rtx-inference.yaml.tpl') as f:
    text = f.read()
for k,v in [('__PROFILE_NAME__','test'),('__BRIDGE__','br9'),
            ('__STORAGE_POOL__','remote'),('__ROOT_SIZE__','100GiB'),
            ('__LIMITS_CPU__','4'),('__LIMITS_MEMORY__','32GiB')]:
    text = text.replace(k,v)
yaml.safe_load(text); print('yaml OK')
"
```

Expected: `yaml OK`.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add profiles/rtx-inference.yaml.tpl
git commit -m "Plan F: cloud-init installs Docker + nvidia-container-toolkit only"
```

---

## Task 4: Refactor `launch-inference.sh` — `.env` generation

**Files:**
- Modify: `launch-inference.sh`

The script currently sed-substitutes ~7 placeholders into the cloud-init template. Plan F removes most of those placeholders, so the sed pipeline shrinks. New responsibility: after the source tree is pushed to the VM, write a `.env` on the VM (mode 0600) with the values that used to be sed-substituted.

- [ ] **Step 1: Trim the sed pipeline**

In `launch-inference.sh`, find the `sed \` block that renders the template. It currently has lines like:

```bash
sed \
  -e "s|__PROFILE_NAME__|$PROFILE_NAME|g" \
  -e "s|__VLLM_MODEL__|$MODEL|g" \
  -e "s|__VLLM_MAX_LEN__|$MAX_LEN|g" \
  -e "s|__VLLM_GPU_UTIL__|$GPU_UTIL|g" \
  -e "s|__VLLM_QUANT__|$QUANTIZATION|g" \
  -e "s|__VLLM_API_KEY_ARG__|$VLLM_API_KEY_ARG|g" \
  -e "s|__BRIDGE__|$BRIDGE|g" \
  -e "s|__STORAGE_POOL__|$STORAGE_POOL|g" \
  -e "s|__ROOT_SIZE__|$ROOT_SIZE|g" \
  -e "s|__LIMITS_CPU__|$CPUS|g" \
  -e "s|__LIMITS_MEMORY__|$MEMORY|g" \
  -e "s|__DDG_MIN_INTERVAL__|$DDG_INTERVAL|g" \
  -e "s|__VLLM_AGENT_API_KEY__|$AGENT_API_KEY|g" \
  "$TEMPLATE" >"$RENDERED"
```

Reduce it to ONLY the placeholders that remain in the new template:

```bash
sed \
  -e "s|__PROFILE_NAME__|$PROFILE_NAME|g" \
  -e "s|__BRIDGE__|$BRIDGE|g" \
  -e "s|__STORAGE_POOL__|$STORAGE_POOL|g" \
  -e "s|__ROOT_SIZE__|$ROOT_SIZE|g" \
  -e "s|__LIMITS_CPU__|$CPUS|g" \
  -e "s|__LIMITS_MEMORY__|$MEMORY|g" \
  "$TEMPLATE" >"$RENDERED"
```

Also remove the `VLLM_API_KEY_ARG` setup block (the `if [[ -n "$API_KEY" ]]; then VLLM_API_KEY_ARG=" --api-key $API_KEY"; ...`) — it's no longer used since vllm's `--api-key` flag is now an env var threaded through `.env`.

- [ ] **Step 2: Replace the post-vLLM-ready section**

The script currently has a section that:
1. Polls vLLM `/v1/models` until ready
2. Tar-pushes the local repo into the VM (Plan B fix)
3. Installs vllm-agent venv inside the VM
4. Starts `vllm-agent.service` via systemd
5. Polls vllm-agent `/health`
6. Updates `.mcp.json`

Replace steps 2-5 (everything between vLLM-ready and `.mcp.json`-update) with the compose-based flow. Find the line `==> Packaging local repo and pushing into VM...` (or similar — the start of the Plan B addition) and replace from there through the end of the vllm-agent install + systemctl restart steps with:

```bash
# ---------- Push local repo into the VM ----------
log "Packaging local repo and pushing into VM..."
TARBALL="$(mktemp -t rtx_5090_dev.XXXXXX.tar.gz)"
tar --exclude='./.git' --exclude='./.worktrees' \
    --exclude='./vllm-agent/.venv' --exclude='./vllm-agent/.venv-agent' \
    --exclude='./mcp-server/.venv' --exclude='./test_apps' \
    --exclude='**/__pycache__' --exclude='**/*.egg-info' \
    -C "$SCRIPT_DIR" -czf "$TARBALL" .

scp -q "$TARBALL" "$LXD_HOST:/tmp/rtx_5090_dev.tar.gz"
rm -f "$TARBALL"
remote "lxc file push /tmp/rtx_5090_dev.tar.gz $VM_NAME/tmp/rtx_5090_dev.tar.gz"
remote "rm /tmp/rtx_5090_dev.tar.gz"

log "Extracting repo into VM at /home/ubuntu/rtx_5090_dev..."
remote "lxc exec $VM_NAME -- bash -c 'set -e; mkdir -p /home/ubuntu/rtx_5090_dev && tar -xzf /tmp/rtx_5090_dev.tar.gz -C /home/ubuntu/rtx_5090_dev && chown -R ubuntu:ubuntu /home/ubuntu/rtx_5090_dev && rm /tmp/rtx_5090_dev.tar.gz'"

# ---------- Generate .env on the VM ----------
log "Writing .env on the VM (mode 0600)..."
remote "lxc exec $VM_NAME -- bash -c 'cat > /home/ubuntu/rtx_5090_dev/.env <<ENV_EOF
VLLM_MODEL=$MODEL
VLLM_QUANT_ARGS=$QUANTIZATION
VLLM_MAX_LEN=$MAX_LEN
VLLM_GPU_UTIL=$GPU_UTIL
VLLM_API_KEY=$API_KEY
VLLM_AGENT_API_KEY=$AGENT_API_KEY
DDG_MIN_INTERVAL_S=$DDG_INTERVAL
ENV_EOF
chown ubuntu:ubuntu /home/ubuntu/rtx_5090_dev/.env
chmod 600 /home/ubuntu/rtx_5090_dev/.env'"

# ---------- Pull images + bring stack up ----------
log "Pulling images (vllm/vllm-openai:latest, python:3.12-slim, nginx:alpine)..."
remote "lxc exec $VM_NAME -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose pull'"
log "Starting compose stack (docker compose up -d)..."
remote "lxc exec $VM_NAME -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose up -d'"
```

- [ ] **Step 3: Update probes**

The script currently probes vLLM at `127.0.0.1:8000/v1/models` and vllm-agent at `127.0.0.1:8088/health` (both inside the VM via `lxc exec ... curl`). With Plan F, both backends are on the compose internal network only. From inside the VM you reach them via nginx on `127.0.0.1:8443`.

Find the existing vLLM probe loop (the one that polls `/v1/models` for up to 90×10s):

```bash
log "Polling vLLM /v1/models (model first-load downloads ~18 GiB on initial run)..."
for i in $(seq 1 90); do
  if remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:$PORT/v1/models" 2>/dev/null | grep -q '"object":"list"'; then
    ...
```

Change `http://127.0.0.1:$PORT/v1/models` to `http://127.0.0.1:8443/v1/models` (going through nginx now). Update the failure-mode `journalctl -u vllm` tail to use `docker logs` instead:

```bash
if ! remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:8443/v1/models" 2>/dev/null | grep -q '"object":"list"'; then
  warn "vLLM API not yet responding after 15 min. Tailing recent vllm container logs:"
  remote "lxc exec $VM_NAME -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose logs --tail=40 vllm'" || true
  die "vLLM did not start in the expected window. Inspect with: ssh $LXD_HOST -- lxc exec $VM_NAME -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose logs -f vllm'"
fi
```

Similarly for the vllm-agent probe loop. Find:

```bash
log "Polling vllm-agent /health..."
for i in $(seq 1 60); do
  if remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:8088/health" 2>/dev/null | grep -q '"ok":true'; then
    ...
```

Change the URL to `http://127.0.0.1:8443/agent/skills` (since `/health` is now vLLM's; vllm-agent's own readiness can be checked via `/agent/skills` which is a quick GET). The grep target changes too:

```bash
log "Polling vllm-agent (via nginx /agent/skills)..."
for i in $(seq 1 60); do
  if remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:8443/agent/skills" 2>/dev/null | grep -q '\['; then
    log "vllm-agent ready after ${i}x5s"
    break
  fi
  sleep 5
done

if ! remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:8443/agent/skills" 2>/dev/null | grep -q '\['; then
  warn "vllm-agent not yet responding after 5 min. Tailing recent container logs:"
  remote "lxc exec $VM_NAME -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose logs --tail=40 vllm-agent'" || true
  warn "vllm-agent will be unavailable; agent_run(mode=remote) will return errors. Investigate with:"
  warn "  ssh $LXD_HOST -- lxc exec $VM_NAME -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose logs -f vllm-agent'"
fi
```

Note: `/agent/skills` returns a JSON array (`[...]`); `grep -q '\['` is a cheap "is it valid array-shaped" check. If `VLLM_AGENT_API_KEY` is set, this probe will get 401 and fail — change it to also include `-H "Authorization: Bearer $AGENT_API_KEY"` if `AGENT_API_KEY` is non-empty. Add this to the curl invocation:

```bash
AUTH_HEADER=""
[[ -n "$AGENT_API_KEY" ]] && AUTH_HEADER="-H 'Authorization: Bearer $AGENT_API_KEY'"
remote "lxc exec $VM_NAME -- bash -c \"curl -s --max-time 3 $AUTH_HEADER http://127.0.0.1:8443/agent/skills\""
```

(Same applies to the failure check after the loop.)

- [ ] **Step 4: Update the `.mcp.json` updater**

The python heredoc that updates `.mcp.json` already takes the unified URLs as args (we can finalize this here). Make sure the URLs written are:

- `VLLM_BASE_URL=http://${VM_IP}:8443`
- `VLLM_AGENT_URL=http://${VM_IP}:8443/agent`

Find the `python3 -` heredoc invocation that updates `.mcp.json`. The 2nd arg is the vLLM base URL and the 6th is the agent URL. Currently they probably look like `"http://${VM_IP}:${PORT}"` and `"http://${VM_IP}:8088"`. Change to:

```bash
python3 - "$MCP_JSON" "http://${VM_IP}:8443" "$MODEL" "$DDG_INTERVAL" "$API_KEY" "http://${VM_IP}:8443/agent" "$AGENT_API_KEY" <<'PYEOF'
```

(The Python heredoc body itself doesn't need changes — it just stores the strings.)

- [ ] **Step 5: Update the example MCP config printed at the end + summary**

Find the final summary block that prints `Endpoint:` and `Agent endpoint:` lines and the example `mcpServers` block. Update them to use the unified `:8443` + `/agent` shape:

```
  Endpoint:        http://${VM_IP}:8443           (vLLM via nginx)
  Agent endpoint:  http://${VM_IP}:8443/agent     (vllm-agent via nginx)
```

In the printed example MCP config, set `VLLM_BASE_URL` to `"http://${VM_IP}:8443"` and `VLLM_AGENT_URL` to `"http://${VM_IP}:8443/agent"`. Keep the conditional `VLLM_AGENT_API_KEY` injection from Plan D unchanged.

- [ ] **Step 6: Bash syntax check**

```bash
bash -n /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/launch-inference.sh && echo "bash OK"
```

Expected: `bash OK`.

- [ ] **Step 7: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add launch-inference.sh
git commit -m "Plan F: launch script writes .env, runs docker compose up; probes via nginx :8443"
```

---

## Task 5: README + CLAUDE.md updates

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `README.md` Components section**

Find the `## Components` section. Replace the existing 3-bullet list (vLLM, vllm-agent, mcp-server) with:

```markdown
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
```

- [ ] **Step 2: Update `README.md` Provisioning section**

Find the bullets under "## Provisioning" describing what `launch-inference.sh` does. Replace with:

```markdown
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
```

- [ ] **Step 3: Update the URL layout table (if Plan E added one)**

If a `## URL layout` section exists from Plan E, leave the table content unchanged — the routing is the same. Update the surrounding prose if it mentions "nginx as a systemd unit" to "nginx as a compose service". Otherwise add the table:

```markdown
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
```

- [ ] **Step 4: Update `CLAUDE.md` (small)**

`CLAUDE.md` currently describes the routing guidance. Add ONE line to the
"## Existing tools (also available)" section noting the deploy shape:

```markdown
The deployment is a docker compose stack inside an LXD VM (vllm, vllm-agent,
nginx) — see README for details. From the orchestrator's perspective the
tool surface is unchanged.
```

(Place it as the last paragraph of the "Existing tools" section, or wherever feels natural.)

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add README.md CLAUDE.md
git commit -m "Plan F: README + CLAUDE.md document compose architecture"
```

---

## Task 6: Live dogfood — full reprovision

This task validates the whole stack against the live LXD host. It is **destructive** — wipes the existing rtx5090 VM and recreates it from current main with the compose stack.

**Files:** none modified by this task

- [ ] **Step 1: Confirm prior commits land**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git log --oneline 4573810..HEAD | head -10
```

Expected: at least 5 commits from Tasks 1-5 above.

- [ ] **Step 2: Reprovision**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
KEY=$(cat /tmp/vllm-agent-key.txt 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
nohup ./launch-inference.sh --lxd-host bdx@192.168.7.11 --agent-api-key "$KEY" > /tmp/launch-inference.log 2>&1 &
LAUNCH_PID=$!
echo "launched pid=$LAUNCH_PID"
echo "$KEY" > /tmp/vllm-agent-key.txt && chmod 600 /tmp/vllm-agent-key.txt
```

(`nohup` so the process survives a Claude Code restart, mirroring the Plan D pattern.)

- [ ] **Step 3: Wait for completion**

The launch script will run for ~10-20 min on first deploy (Docker installs add a few minutes vs. the venv-based path; first model download is the dominant cost). Watch via:

```bash
tail -f /tmp/launch-inference.log | grep -v 'Unpacking image'
```

Wait for the `Endpoint:        http://...:8443` line OR a `XX  ...` failure line.

- [ ] **Step 4: Verify the unified endpoint**

```bash
VM_IP=$(python3 -c "import json,urllib.parse as u; print(u.urlparse(json.load(open('/home/bdx/allcode/github/vantagecompute/rtx_5090_dev/.mcp.json'))['mcpServers']['vllm-rtx5090']['env']['VLLM_BASE_URL']).hostname)")
KEY=$(python3 -c "import json; print(json.load(open('/home/bdx/allcode/github/vantagecompute/rtx_5090_dev/.mcp.json'))['mcpServers']['vllm-rtx5090']['env'].get('VLLM_AGENT_API_KEY',''))")

echo "VM_IP=$VM_IP"
echo "key set: $([[ -n "$KEY" ]] && echo yes || echo no)"

# Health (no auth)
curl -s -o /dev/null -w "/health             %{http_code}\n" http://${VM_IP}:8443/health

# vLLM (no auth gate by default)
curl -s -o /dev/null -w "/v1/models          %{http_code}\n" http://${VM_IP}:8443/v1/models

# Agent without auth (expect 401)
curl -s -o /dev/null -w "/agent/skills (401) %{http_code}\n" http://${VM_IP}:8443/agent/skills

# Agent with auth (expect 200)
curl -s -o /dev/null -w "/agent/skills (200) %{http_code}\n" -H "Authorization: Bearer $KEY" http://${VM_IP}:8443/agent/skills

# Confirm backends are NOT reachable directly (compose internal-only)
curl -s -o /dev/null -w "/v1 direct :8000   %{http_code}\n" --max-time 3 http://${VM_IP}:8000/v1/models
curl -s -o /dev/null -w "/agent direct :8088 %{http_code}\n" --max-time 3 http://${VM_IP}:8088/health
```

Expected:
```
/health             200
/v1/models          200
/agent/skills (401) 401
/agent/skills (200) 200
/v1 direct :8000    000  (connection refused — backend not exposed)
/agent direct :8088 000  (connection refused)
```

If anything else, capture `docker compose logs` from the VM and investigate.

- [ ] **Step 5: Verify GPU is visible inside the vllm container**

```bash
ssh bdx@192.168.7.11 -- 'lxc exec rtx5090 -- su - ubuntu -c "cd ~/rtx_5090_dev && docker compose exec vllm nvidia-smi -L"'
```

Expected: prints the 5090's PCI address (`GPU 0: NVIDIA GeForce RTX 5090 (UUID: ...)`). If `nvidia-smi: command not found` or no GPU listed, nvidia-container-toolkit isn't wired correctly and `gpus: all` isn't taking effect.

- [ ] **Step 6: agent_run smoke (if Claude Code is restarted to pick up new .mcp.json)**

This step is optional — the curl smoke in Step 4 is sufficient functional validation. If you want the full Claude-MCP-shim path tested, restart Claude Code and run `mcp__vllm-rtx5090__agent_run` against the new VM.

- [ ] **Step 7: No new commit**

This task validates; nothing to commit.

---

## Final verification (after Tasks 1-6)

- `git log --oneline 4573810..HEAD` shows commits from Tasks 1-5 (5 commits).
- `docker compose config` produces no errors against a placeholder `.env`.
- `nginx -t` passes against `compose/nginx.conf` (in the nginx:alpine container).
- YAML/Bash lints pass.
- Live VM is up; `:8443` reachable; backends NOT directly reachable; auth gates correct on `/agent/*`; `nvidia-smi` works inside the vllm container.

---

## Self-Review

### Spec coverage

| Spec section | Tasks |
|---|---|
| §2 Architecture (3 services, internal network, single port) | Tasks 1, 3, 4 |
| §3 Compose service definitions | Task 1 |
| §4 nginx config + auth flow (header pass-through) | Task 1 |
| §5 .env / secrets model | Tasks 2, 4 |
| §6 Cloud-init flow (Docker + ncc-toolkit) | Task 3 |
| §6 Launch-script flow (.env gen + compose up) | Task 4 |
| §6 Probes through nginx | Task 4 |
| §7 Offline checks | Tasks 1 (compose config), 1 (nginx -t), 3 (yaml), 4 (bash) |
| §7 Live checks | Task 6 |
| §7 Documentation | Task 5 |

### Placeholder scan

No "TBD", "TODO", or "fill in details" tokens. Every code/command step has full content. Two soft phrases I deliberately kept:

- Task 3 Step 2: "If the existing template has additional fields..." — the implementer is expected to read the existing file once (Step 1) and merge any project-specific bits forward. This is a real consideration, not a placeholder.
- Task 4 Step 2: "Find the line `==> Packaging local repo and pushing into VM...`" — the implementer needs to locate the right block in the existing script. Not a placeholder; a navigational hint.

### Type / signature consistency

- `.env` variable names consistent across `.env.example`, `compose.yaml`, and `launch-inference.sh`'s heredoc generator: `VLLM_MODEL`, `VLLM_QUANT_ARGS`, `VLLM_MAX_LEN`, `VLLM_GPU_UTIL`, `VLLM_API_KEY`, `VLLM_AGENT_API_KEY`, `DDG_MIN_INTERVAL_S`. Verified.
- Compose service names (`vllm`, `vllm-agent`, `nginx`) consistent across `compose.yaml`, `nginx.conf` (`proxy_pass http://vllm:8000`, `proxy_pass http://vllm-agent:8088/`), and the script's `docker compose logs` invocations.
- Volume names (`hf-cache`, `vllm-agent-sessions`, `vllm-agent-runs`) defined once in `compose.yaml`'s `volumes:` block and referenced from the service `volumes:` lists.
- Port `8443` consistent across `nginx.conf`'s `listen`, `compose.yaml`'s `ports:`, the launch script's `.mcp.json` updater, the README URL table, and Task 6 verification commands.

No mismatches found.
