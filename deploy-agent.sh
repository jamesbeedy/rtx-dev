#!/usr/bin/env bash
# deploy-agent.sh — sync vllm-agent source to the VM and restart the container.
# Does NOT touch vllm (model stays loaded) or reprovision anything.
#
# Usage: ./deploy-agent.sh --lxd-host USER@HOST [--vm-name NAME]

set -euo pipefail

LXD_HOST=""
VM_NAME="rtx5090"

usage() {
  cat <<EOF
Usage: $0 --lxd-host USER@HOST [--vm-name NAME]

  --lxd-host USER@HOST   SSH target for the LXD cluster member.
  --vm-name  NAME        LXD VM name (default: rtx5090).
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --lxd-host) LXD_HOST="$2"; shift 2;;
    --vm-name)  VM_NAME="$2";  shift 2;;
    -h|--help)  usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2;;
  esac
done

[[ -z "$LXD_HOST" ]] && { echo "ERROR: --lxd-host is required" >&2; usage >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$LXD_HOST" "$@"; }

echo "==> Packing vllm-agent source..."
TARBALL="$(mktemp /tmp/vllm-agent-XXXXXX.tar.gz)"
tar -czf "$TARBALL" \
  --exclude=".git" \
  --exclude="__pycache__" \
  --exclude="*.egg-info" \
  --exclude=".venv" \
  --exclude="dist" \
  --exclude="build" \
  -C "$SCRIPT_DIR" \
  vllm-agent
trap 'rm -f "$TARBALL"' EXIT

echo "==> Pushing to $LXD_HOST → $VM_NAME:/tmp/vllm-agent.tar.gz..."
scp -q "$TARBALL" "$LXD_HOST:/tmp/vllm-agent.tar.gz"
remote "lxc file push /tmp/vllm-agent.tar.gz $VM_NAME/tmp/vllm-agent.tar.gz && rm /tmp/vllm-agent.tar.gz"

echo "==> Extracting in VM..."
remote "lxc exec $VM_NAME -- bash -c '
  set -e
  rm -rf /home/ubuntu/rtx_5090_dev/vllm-agent
  tar -xzf /tmp/vllm-agent.tar.gz -C /home/ubuntu/rtx_5090_dev
  chown -R ubuntu:ubuntu /home/ubuntu/rtx_5090_dev/vllm-agent
  rm /tmp/vllm-agent.tar.gz
'"

echo "==> Restarting vllm-agent container..."
remote "lxc exec $VM_NAME -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose restart vllm-agent'"

echo "==> Waiting for vllm-agent to be healthy (up to 120s)..."
for i in $(seq 1 60); do
  status=$(remote "lxc exec $VM_NAME -- curl -so /dev/null -w '%{http_code}' http://localhost:8443/agent/skills" 2>/dev/null || true)
  if [[ "$status" == "200" || "$status" == "401" ]]; then
    echo "==> vllm-agent is up."
    exit 0
  fi
  sleep 2
done

echo "ERROR: vllm-agent did not come up after 120s. Check logs:" >&2
echo "  ssh $LXD_HOST \"lxc exec $VM_NAME -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose logs vllm-agent --tail 50'\"" >&2
exit 1
