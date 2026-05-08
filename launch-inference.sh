#!/usr/bin/env bash
# launch-inference.sh — provision an LXD VM with GPU passthrough running vLLM.
# Assumes the LXD host already has the GPU bound to vfio-pci. See the README
# (or the prior session log) for host-side passthrough setup.

set -euo pipefail

# ---------- defaults ----------
LXD_HOST=""
VM_NAME="rtx5090"
ROOT_SIZE="100GiB"
MEMORY="32GiB"
CPUS="4"
BRIDGE="br9"
STORAGE_POOL="remote"
MODEL="QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ"
MAX_LEN="32768"
GPU_UTIL="0.92"
QUANTIZATION="awq_marlin --enable-auto-tool-choice --tool-call-parser qwen3_coder"
API_KEY=""
AGENT_API_KEY=""    # optional auth for vllm-agent serve (Bearer); default: no auth
PORT="8000"
TARGET=""           # auto-detect from hostname of LXD_HOST
GPU_PCI=""          # auto-detect from lspci on LXD_HOST
PROFILE_NAME=""     # default: inference-${VM_NAME}
DDG_INTERVAL="1.5"  # seconds between DDG searches in the local MCP server

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/profiles/rtx-inference.yaml.tpl"

usage() {
  cat <<EOF
Usage: $0 --lxd-host USER@HOST [options]

Required:
  --lxd-host USER@HOST     SSH target for the LXD cluster member with the GPU.

Common options (defaults shown):
  --vm-name $VM_NAME
  --model $MODEL
  --root-size $ROOT_SIZE
  --memory $MEMORY
  --cpus $CPUS
  --bridge $BRIDGE
  --storage-pool $STORAGE_POOL
  --max-len $MAX_LEN
  --gpu-util $GPU_UTIL
  --quantization $QUANTIZATION
  --api-key SECRET            Enable Bearer auth on vLLM (default: no auth).
  --agent-api-key SECRET      Enable Bearer auth on vllm-agent serve.
                              (Default: no auth — only safe on a trusted LAN.)
  --port $PORT
  --target NODE               LXD cluster member name (default: \$(hostname) on lxd-host).
  --gpu-pci 0000:XX:00.0      Override autodetected GPU PCI address.
  --profile-name NAME         Default: inference-<vm-name>.
  --ddg-interval SECONDS      Min seconds between DuckDuckGo searches in the
                              local MCP server (lands as DDG_MIN_INTERVAL_S in
                              .mcp.json env). Default: $DDG_INTERVAL.

After successful provisioning, prints an example MCP server config you can
drop into ~/.config/claude-code/mcp.json (or equivalent).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lxd-host)       LXD_HOST="$2"; shift 2;;
    --vm-name)        VM_NAME="$2"; shift 2;;
    --root-size)      ROOT_SIZE="$2"; shift 2;;
    --memory)         MEMORY="$2"; shift 2;;
    --cpus)           CPUS="$2"; shift 2;;
    --bridge)         BRIDGE="$2"; shift 2;;
    --storage-pool)   STORAGE_POOL="$2"; shift 2;;
    --model)          MODEL="$2"; shift 2;;
    --max-len)        MAX_LEN="$2"; shift 2;;
    --gpu-util)       GPU_UTIL="$2"; shift 2;;
    --quantization)   QUANTIZATION="$2"; shift 2;;
    --api-key)        API_KEY="$2"; shift 2;;
    --agent-api-key)  AGENT_API_KEY="$2"; shift 2;;
    --port)           PORT="$2"; shift 2;;
    --target)         TARGET="$2"; shift 2;;
    --gpu-pci)        GPU_PCI="$2"; shift 2;;
    --profile-name)   PROFILE_NAME="$2"; shift 2;;
    --ddg-interval)   DDG_INTERVAL="$2"; shift 2;;
    -h|--help)        usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2;;
  esac
done

[[ -z "$LXD_HOST"  ]] && { echo "ERROR: --lxd-host is required" >&2; usage >&2; exit 2; }
[[ -f "$TEMPLATE"  ]] || { echo "ERROR: template not found: $TEMPLATE" >&2; exit 2; }
[[ -z "$PROFILE_NAME" ]] && PROFILE_NAME="inference-${VM_NAME}"

log()  { printf '==> %s\n' "$*" >&2; }
warn() { printf '!!  %s\n' "$*" >&2; }
die()  { printf 'XX  %s\n' "$*" >&2; exit 1; }

# remote: run a command on the LXD host over SSH
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$LXD_HOST" "$@"; }

# ---------- 1. Verify SSH + LXD reachable ----------
log "Checking SSH to $LXD_HOST and LXD availability..."
remote 'lxc --version >/dev/null' || die "Cannot run lxc on $LXD_HOST. Check SSH and LXD install."
LXD_VERSION="$(remote 'lxc --version')"
log "LXD on $LXD_HOST: $LXD_VERSION"

# ---------- 2. Detect target node + GPU PCI ----------
if [[ -z "$TARGET" ]]; then
  TARGET="$(remote hostname)"
  log "Auto-detected target cluster member: $TARGET"
fi

if [[ -z "$GPU_PCI" ]]; then
  log "Auto-detecting NVIDIA GPU bound to vfio-pci on $TARGET..."
  GPU_PCI="$(remote "lspci -nnk | awk '/NVIDIA Corporation/ && /\[0300\]/ {dev=\$1; getline; getline; if (/Kernel driver in use: vfio-pci/) print dev}' | head -1")"
  [[ -n "$GPU_PCI" ]] || die "No NVIDIA GPU bound to vfio-pci found on $TARGET. Configure host passthrough first."
  GPU_PCI="0000:$GPU_PCI"
  log "Detected GPU at $GPU_PCI"
fi

# ---------- 3. Verify bridge + storage pool ----------
remote "ip link show $BRIDGE >/dev/null 2>&1" || die "Bridge $BRIDGE not found on $TARGET."
remote "lxc storage show $STORAGE_POOL >/dev/null 2>&1" || die "LXD storage pool '$STORAGE_POOL' not found."

# ---------- 4. Render profile from template ----------
if [[ -n "$API_KEY" ]]; then
  VLLM_API_KEY_ARG=" --api-key $API_KEY"
else
  VLLM_API_KEY_ARG=""
fi

RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT

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

log "Rendered profile to $RENDERED ($(wc -l <"$RENDERED") lines)"

# ---------- 5. Apply profile (create or replace) ----------
if remote "lxc profile show $PROFILE_NAME >/dev/null 2>&1"; then
  log "Updating existing LXD profile: $PROFILE_NAME"
else
  log "Creating LXD profile: $PROFILE_NAME"
  remote "lxc profile create $PROFILE_NAME"
fi
remote "lxc profile edit $PROFILE_NAME" <"$RENDERED"

# ---------- 6. Delete existing VM if present ----------
if remote "lxc info $VM_NAME >/dev/null 2>&1"; then
  warn "VM $VM_NAME already exists — stopping and deleting for reprovision."
  remote "lxc stop $VM_NAME --force 2>/dev/null || true"
  remote "lxc delete $VM_NAME --force"
fi

# ---------- 7. Init VM with profile ----------
log "Initialising VM $VM_NAME on $TARGET with profile $PROFILE_NAME..."
remote "lxc init ubuntu:24.04 $VM_NAME --vm --target $TARGET --profile default --profile $PROFILE_NAME"

# ---------- 8. Attach GPU passthrough as a per-instance pci device ----------
# (Using lxc 'pci' device type — 'gpu' device type sets x-vga=on which Blackwell rejects.)
log "Attaching GPU $GPU_PCI to $VM_NAME..."
remote "lxc config device add $VM_NAME gpu pci address=$GPU_PCI"

# ---------- 9. Start VM ----------
log "Starting $VM_NAME..."
remote "lxc start $VM_NAME"

# ---------- 10. Wait for the agent (first boot before cloud-init reboot) ----------
log "Waiting for VM agent (first boot)..."
for i in $(seq 1 60); do
  if remote "lxc exec $VM_NAME -- true 2>/dev/null"; then
    log "VM agent up (first boot, ${i}x2s)"
    break
  fi
  sleep 2
done
remote "lxc exec $VM_NAME -- true" || die "VM agent never came up on first boot."

# ---------- 11. Wait for cloud-init done, then survive its reboot ----------
log "Waiting for cloud-init to finish (this includes nvidia-open install + vllm pip install; can take ~5–10 min)..."
# cloud-init status --wait blocks until done; if power_state reboot fires mid-call, lxc exec returns non-zero and we just retry the loop.
for attempt in 1 2 3 4 5; do
  if remote "lxc exec $VM_NAME -- cloud-init status --wait" 2>/dev/null; then
    break
  fi
  log "  cloud-init wait interrupted (attempt $attempt) — VM likely rebooting; retrying in 15s"
  sleep 15
done

# ---------- 12. Wait for VM to come back from cloud-init's reboot ----------
log "Waiting for VM to come back from cloud-init reboot..."
sleep 5
for i in $(seq 1 90); do
  if remote "lxc exec $VM_NAME -- true 2>/dev/null"; then
    log "VM agent up after reboot (${i}x2s)"
    break
  fi
  sleep 2
done
remote "lxc exec $VM_NAME -- true" || die "VM did not return after cloud-init reboot."

# ---------- 13. Poll vLLM /v1/models until ready ----------
log "Polling vLLM /v1/models (model first-load downloads ~18 GiB on initial run)..."
for i in $(seq 1 90); do
  if remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:$PORT/v1/models" 2>/dev/null | grep -q '"object":"list"'; then
    log "vLLM API ready after ${i}x10s"
    break
  fi
  if (( i % 6 == 0 )); then
    log "  ...still loading (~$((i*10))s elapsed). Recent log:"
    remote "lxc exec $VM_NAME -- bash -c 'journalctl -u vllm -n 3 --no-pager 2>/dev/null | tail -3'" || true
  fi
  sleep 10
done

if ! remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:$PORT/v1/models" 2>/dev/null | grep -q '"object":"list"'; then
  warn "vLLM API not yet responding after 15 min. Tailing the last 40 lines of the journal:"
  remote "lxc exec $VM_NAME -- journalctl -u vllm -n 40 --no-pager" || true
  die "vLLM did not start in the expected window. Inspect with: ssh $LXD_HOST -- lxc exec $VM_NAME -- journalctl -u vllm -f"
fi

# ---------- 14. Push local repo into VM and install vllm-agent ----------
# (Avoids needing GitHub auth in the VM for private repos.)
log "Packaging local repo and pushing into VM..."
TARBALL="$(mktemp -t rtx_5090_dev.XXXXXX.tar.gz)"
tar --exclude='./.git' --exclude='./.worktrees' \
    --exclude='./vllm-agent/.venv' --exclude='./vllm-agent/.venv-agent' \
    --exclude='./mcp-server/.venv' --exclude='./test_apps' \
    --exclude='**/__pycache__' --exclude='**/*.egg-info' \
    -C "$SCRIPT_DIR" -czf "$TARBALL" .

# scp the tarball to the LXD host, then lxc-file-push it into the VM.
scp -q "$TARBALL" "$LXD_HOST:/tmp/rtx_5090_dev.tar.gz"
rm -f "$TARBALL"
remote "lxc file push /tmp/rtx_5090_dev.tar.gz $VM_NAME/tmp/rtx_5090_dev.tar.gz"
remote "rm /tmp/rtx_5090_dev.tar.gz"

log "Extracting and installing vllm-agent in VM..."
remote "lxc exec $VM_NAME -- bash -c 'set -e; mkdir -p /home/ubuntu/rtx_5090_dev && tar -xzf /tmp/rtx_5090_dev.tar.gz -C /home/ubuntu/rtx_5090_dev && chown -R ubuntu:ubuntu /home/ubuntu/rtx_5090_dev && rm /tmp/rtx_5090_dev.tar.gz'"
remote "lxc exec $VM_NAME -- su - ubuntu -c 'cd /home/ubuntu/rtx_5090_dev/vllm-agent && python3 -m venv .venv-agent && .venv-agent/bin/pip install --quiet --upgrade pip && .venv-agent/bin/pip install --quiet -e .'"

log "Starting vllm-agent.service..."
remote "lxc exec $VM_NAME -- bash -c 'systemctl reset-failed vllm-agent.service 2>/dev/null || true; systemctl restart vllm-agent.service'"

# ---------- 15. Poll vllm-agent /health until ready ----------
log "Polling vllm-agent /health..."
for i in $(seq 1 60); do
  if remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:8088/health" 2>/dev/null | grep -q '"ok":true'; then
    log "vllm-agent ready after ${i}x5s"
    break
  fi
  sleep 5
done

if ! remote "lxc exec $VM_NAME -- curl -s --max-time 3 http://127.0.0.1:8088/health" 2>/dev/null | grep -q '"ok":true'; then
  warn "vllm-agent not yet responding after 5 min. Tailing the journal:"
  remote "lxc exec $VM_NAME -- journalctl -u vllm-agent -n 30 --no-pager" || true
  warn "vllm-agent will be unavailable; agent_run(mode=remote) will return errors. Investigate with:"
  warn "  ssh $LXD_HOST -- lxc exec $VM_NAME -- journalctl -u vllm-agent -f"
fi

# ---------- 15. Capture VM IP (now that networking is fully up) ----------
VM_IP=""
for i in 1 2 3 4 5; do
  VM_IP="$(remote "lxc list $VM_NAME -c 4 -f csv | head -1 | awk '{print \$1}'")"
  [[ -n "$VM_IP" ]] && break
  sleep 2
done
[[ -z "$VM_IP" ]] && warn "Could not detect VM IP; endpoint URL below will be incomplete."
log "VM IP: $VM_IP"

# ---------- 16. Smoke test ----------
log "Running smoke test against /v1/chat/completions..."
AUTH_HEADER=""
[[ -n "$API_KEY" ]] && AUTH_HEADER="-H 'Authorization: Bearer $API_KEY'"
remote "lxc exec $VM_NAME -- curl -s $AUTH_HEADER http://127.0.0.1:$PORT/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with: ready\"}],\"max_tokens\":8,\"temperature\":0}'" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print('  reply:', r['choices'][0]['message']['content'])" || warn "Smoke test failed; API is up but completion call did not parse cleanly."

# ---------- 17. Update local .mcp.json (so MCP server picks up new config) ----------
MCP_JSON="$SCRIPT_DIR/.mcp.json"
if [[ -f "$MCP_JSON" ]]; then
  log "Updating $MCP_JSON with VLLM_BASE_URL=http://${VM_IP}:${PORT}, VLLM_MODEL=$MODEL, DDG_MIN_INTERVAL_S=$DDG_INTERVAL..."
  python3 - "$MCP_JSON" "http://${VM_IP}:${PORT}" "$MODEL" "$DDG_INTERVAL" "$API_KEY" "http://${VM_IP}:8088" "$AGENT_API_KEY" <<'PYEOF'
import json, sys
path, base_url, model, ddg_interval, api_key, agent_url, agent_api_key = sys.argv[1:8]
with open(path) as f:
    cfg = json.load(f)
servers = cfg.setdefault("mcpServers", {})
# update the first vllm-* server we find, or create vllm-rtx5090
key = next((k for k in servers if k.startswith("vllm-")), "vllm-rtx5090")
entry = servers.setdefault(key, {})
env = entry.setdefault("env", {})
env["VLLM_BASE_URL"] = base_url
env["VLLM_MODEL"] = model
env["VLLM_AGENT_URL"] = agent_url
env["DDG_MIN_INTERVAL_S"] = ddg_interval
if api_key:
    env["VLLM_API_KEY"] = api_key
elif "VLLM_API_KEY" in env:
    del env["VLLM_API_KEY"]
if agent_api_key:
    env["VLLM_AGENT_API_KEY"] = agent_api_key
elif "VLLM_AGENT_API_KEY" in env:
    del env["VLLM_AGENT_API_KEY"]
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"  updated server entry: {key}")
PYEOF
else
  log "No $MCP_JSON found; skipping local config update."
fi

# ---------- 18. Done — show endpoint + MCP config ----------
ENDPOINT="http://${VM_IP}:${PORT}"
AGENT_KEY_LINE=""
if [[ -n "$AGENT_API_KEY" ]]; then
  AGENT_KEY_LINE=$',\n          "VLLM_AGENT_API_KEY": "'"$AGENT_API_KEY"'"'
fi
cat <<EOF

============================================================
 vLLM ready
============================================================
  Endpoint:        $ENDPOINT
  Agent endpoint:  http://${VM_IP}:8088
  Model:           $MODEL
  Max model len:   $MAX_LEN
  API key:         ${API_KEY:-(none)}
  VM:              $VM_NAME on $TARGET (GPU $GPU_PCI)
  Profile:         $PROFILE_NAME
  Tail logs:       ssh $LXD_HOST -- lxc exec $VM_NAME -- journalctl -u vllm -f

To register as an MCP server in Claude Code, add to your MCP config
(e.g. ~/.config/claude-code/mcp.json or wherever your client reads it):

  {
    "mcpServers": {
      "vllm-${VM_NAME}": {
        "command": "$SCRIPT_DIR/mcp-server/.venv/bin/python",
        "args": ["$SCRIPT_DIR/mcp-server/vllm_mcp.py"],
        "env": {
          "VLLM_BASE_URL": "$ENDPOINT",
          "VLLM_MODEL": "$MODEL",
          "VLLM_AGENT_URL": "http://${VM_IP}:8088",
          "DDG_MIN_INTERVAL_S": "$DDG_INTERVAL"$( [[ -n "$API_KEY" ]] && printf ',\n          "VLLM_API_KEY": "%s"' "$API_KEY")${AGENT_KEY_LINE}
        }
      }
    }
  }

If $SCRIPT_DIR/.mcp.json exists, it has been updated automatically.
============================================================
EOF
