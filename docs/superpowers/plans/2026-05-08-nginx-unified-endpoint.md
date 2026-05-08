# Plan E: nginx unified endpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put nginx in front of vLLM (`:8000`) and vllm-agent (`:8088`) inside the LXD VM with path-prefix routing — `/v1/*` → vLLM, `/agent/*` → vllm-agent — so the only externally-reachable port is **`8443`**. Both backends bind to `127.0.0.1` only, so the unification is *enforced*, not just *offered*.

**Architecture:**
- nginx listens on `0.0.0.0:8443` inside the VM
- vLLM rebinds to `127.0.0.1:8000` (was `0.0.0.0:8000`)
- vllm-agent rebinds to `127.0.0.1:8088` (was `0.0.0.0:8088`)
- nginx routes `/v1/*` → `http://127.0.0.1:8000` (no rewrite — vLLM already uses `/v1/*` natively)
- nginx routes `/agent/*` → `http://127.0.0.1:8088/` (trailing slash on `proxy_pass` strips the `/agent` prefix so vllm-agent sees `/run`, `/session/*`, `/skills`, `/artifacts` unchanged)
- nginx routes `/health` → `http://127.0.0.1:8000/health` (vLLM liveness for orchestration probes)
- TLS deferred to a follow-up plan; HTTP-only for now

**Tech Stack:** nginx (Ubuntu apt package), systemd, cloud-init runcmd. No code changes to `vllm-agent` or `mcp-server` — only env-var values change in `.mcp.json`.

**Source spec:** This plan; the existing path layout in `vllm-agent/src/vllm_agent/server.py` and `mcp-server/vllm_mcp.py` is the spec.

---

## File Structure

```
rtx_5090_dev/
├── profiles/
│   └── rtx-inference.yaml.tpl         # MODIFY: + nginx package + nginx config + bind 127.0.0.1
├── launch-inference.sh                 # MODIFY: .mcp.json env vars use unified :8443 URLs
└── README.md                           # MODIFY: document single-port architecture
```

No new files. No Python changes (`mcp-server` and `vllm-agent` see new URLs via env vars, no code edits needed).

---

## Verification of "no Python changes needed"

This is the load-bearing claim of Plan E. Spot-check the URL builders:

**`mcp-server/vllm_mcp.py`** builds:
- `f"{VLLM_BASE_URL}/health"` — with `VLLM_BASE_URL=http://VM:8443` → `http://VM:8443/health` → nginx `/health` route → vLLM:8000/health ✓
- `f"{VLLM_BASE_URL}/v1/models"` — → `http://VM:8443/v1/models` → nginx `/v1/` route → vLLM:8000/v1/models ✓
- `f"{VLLM_BASE_URL}/v1/chat/completions"` — same pattern ✓
- `f"{VLLM_AGENT_URL}/run"` — with `VLLM_AGENT_URL=http://VM:8443/agent` → `http://VM:8443/agent/run` → nginx `/agent/` route (strips prefix) → vllm-agent:8088/run ✓
- `f"{VLLM_AGENT_URL}/session/{sid}/step"` — `http://VM:8443/agent/session/.../step` → vllm-agent:8088/session/.../step ✓
- `f"{VLLM_AGENT_URL}/artifacts?out_dir=...&tail_lines=..."` — `http://VM:8443/agent/artifacts?...` → vllm-agent:8088/artifacts?... ✓

**`vllm-agent/src/vllm_agent/loop.py`** builds:
- `f"{cfg.vllm_base_url}/v1/chat/completions"` — exclusively. Same pattern as above. ✓

`Authorization: Bearer` headers pass through nginx unchanged (nginx forwards all client headers via `proxy_pass` by default). API key auth still works.

---

## Task 1: Update cloud-init template — nginx + bind to localhost

**File:** `profiles/rtx-inference.yaml.tpl`

### Step 1: Add nginx to the packages list

Find the `packages:` block at the top of the cloud-init `runcmd`/`packages` section (currently has `python3-venv`). Add `nginx`:

```yaml
    packages:
      - python3-venv
      - nginx
```

### Step 2: Bind vLLM to 127.0.0.1

Find the `vllm.service` heredoc in the runcmd section. Locate the `ExecStart=...` line. Currently has `--host 0.0.0.0`. Change to `--host 127.0.0.1`.

The relevant line is something like:
```
ExecStart=/opt/vllm/bin/vllm serve __VLLM_MODEL__ \
  --port 8000 --host 0.0.0.0 \
  --max-model-len __VLLM_MAX_LEN__ \
  ...
```

Becomes:
```
ExecStart=/opt/vllm/bin/vllm serve __VLLM_MODEL__ \
  --port 8000 --host 127.0.0.1 \
  --max-model-len __VLLM_MAX_LEN__ \
  ...
```

(The `--host` value is the only change. Don't touch other args.)

### Step 3: Bind vllm-agent to 127.0.0.1

Find the `vllm-agent.service` heredoc. Locate the `ExecStart=...` line:
```
ExecStart=/home/ubuntu/rtx_5090_dev/vllm-agent/.venv-agent/bin/vllm-agent serve --host 0.0.0.0 --port 8088
```

Change to:
```
ExecStart=/home/ubuntu/rtx_5090_dev/vllm-agent/.venv-agent/bin/vllm-agent serve --host 127.0.0.1 --port 8088
```

### Step 4: Add the nginx site config

In the `runcmd` block, AFTER the `systemctl enable vllm-agent.service` line and BEFORE the `power_state:` block, add a new step that writes the nginx config and reloads:

```yaml
      # nginx unified endpoint on :8443 — routes /v1/* to vLLM, /agent/* to vllm-agent
      - |
        cat > /etc/nginx/sites-available/rtx-inference <<'EOF'
        server {
            listen 8443;
            server_name _;

            # 30-minute timeout for long agent runs and big completions
            proxy_read_timeout 1800s;
            proxy_send_timeout 60s;
            proxy_connect_timeout 10s;
            client_max_body_size 16m;

            # vLLM (OpenAI-compatible API)
            location /v1/ {
                proxy_pass http://127.0.0.1:8000;
                proxy_http_version 1.1;
                proxy_buffering off;
            }

            # vLLM liveness probe (mcp-server's health() tool calls /health)
            location = /health {
                proxy_pass http://127.0.0.1:8000/health;
            }

            # vllm-agent (path prefix stripped by trailing slash on proxy_pass)
            location /agent/ {
                proxy_pass http://127.0.0.1:8088/;
                proxy_http_version 1.1;
                proxy_buffering off;
            }
        }
        EOF
      - rm -f /etc/nginx/sites-enabled/default
      - ln -sf /etc/nginx/sites-available/rtx-inference /etc/nginx/sites-enabled/rtx-inference
      - nginx -t
      - systemctl enable --now nginx
      - systemctl reload nginx
```

Notes:
- `proxy_buffering off` — important for streaming chat completions
- `proxy_read_timeout 1800s` — matches vllm-agent's default `timeout_s=1800`
- `client_max_body_size 16m` — POST bodies for `/run` can carry `extra_context` files; 16m is generous
- `proxy_http_version 1.1` — lets keep-alive work across the proxy
- We remove `default` site to avoid binding port 80 ambiguity
- `nginx -t` validates the config before reload

### Step 5: Validate the YAML still parses

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev && python3 -c "
import yaml
with open('profiles/rtx-inference.yaml.tpl') as f:
    text = f.read()
for k,v in [('__VLLM_MODEL__','m'),('__PROFILE_NAME__','p'),('__VLLM_MAX_LEN__','32768'),
            ('__VLLM_GPU_UTIL__','0.9'),('__VLLM_QUANT__','awq'),('__VLLM_API_KEY_ARG__',''),
            ('__BRIDGE__','br'),('__STORAGE_POOL__','pool'),('__ROOT_SIZE__','100GiB'),
            ('__LIMITS_CPU__','4'),('__LIMITS_MEMORY__','32GiB'),('__DDG_MIN_INTERVAL__','1.5'),
            ('__VLLM_AGENT_API_KEY__','testkey')]:
    text = text.replace(k,v)
yaml.safe_load(text); print('yaml OK')
"
```

Expected: `yaml OK`. The nginx config block is inside a YAML scalar string — indentation must match the surrounding `- |` cloud-init style.

### Step 6: Commit

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add profiles/rtx-inference.yaml.tpl
git commit -m "Plan E: nginx unified :8443 endpoint; backends bind 127.0.0.1"
```

---

## Task 2: Update `launch-inference.sh` to write unified URLs to `.mcp.json`

**File:** `launch-inference.sh`

### Step 1: Update the `.mcp.json` updater heredoc

Find the `python3 -` heredoc that updates `.mcp.json` (after the vLLM probe section). Currently passes:
```bash
python3 - "$MCP_JSON" "http://${VM_IP}:${PORT}" "$MODEL" "$DDG_INTERVAL" "$API_KEY" "http://${VM_IP}:8088" "$AGENT_API_KEY" <<'PYEOF'
```

`http://${VM_IP}:${PORT}` is the vLLM URL (port 8000). `http://${VM_IP}:8088` is the vllm-agent URL.

Change BOTH to use the unified port. The 2nd argument becomes `http://${VM_IP}:8443` and the 6th argument becomes `http://${VM_IP}:8443/agent`:

```bash
python3 - "$MCP_JSON" "http://${VM_IP}:8443" "$MODEL" "$DDG_INTERVAL" "$API_KEY" "http://${VM_IP}:8443/agent" "$AGENT_API_KEY" <<'PYEOF'
```

The python heredoc body doesn't need changes — it just stores the strings. The unpack remains:
```python
path, base_url, model, ddg_interval, api_key, agent_url, agent_api_key = sys.argv[1:8]
```

### Step 2: Update the example MCP config printed at the end

Find the final `cat <<EOF` block that prints the sample `mcpServers` config. Update the env block:

Find:
```
        "VLLM_BASE_URL": "$ENDPOINT",
```
where `$ENDPOINT` was set to `http://${VM_IP}:${PORT}`. Change `$ENDPOINT` (defined earlier in the script) to:
```bash
ENDPOINT="http://${VM_IP}:8443"
```

Find any line that references the agent endpoint, e.g.:
```
"VLLM_AGENT_URL": "http://${VM_IP}:8088"
```
Change to:
```
"VLLM_AGENT_URL": "http://${VM_IP}:8443/agent"
```

### Step 3: Update the summary block

Find the final summary printout that lists `Endpoint:` and `Agent endpoint:`. Currently:
```
  Endpoint:        $ENDPOINT
  Agent endpoint:  http://${VM_IP}:8088
```

Change to make it explicit that both go through nginx:
```
  Endpoint:        http://${VM_IP}:8443           (vLLM via nginx)
  Agent endpoint:  http://${VM_IP}:8443/agent     (vllm-agent via nginx)
```

### Step 4: Bash syntax check

```bash
bash -n /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/launch-inference.sh && echo "bash OK"
```

### Step 5: Commit

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add launch-inference.sh
git commit -m "Plan E: launch script writes unified :8443/agent URLs to .mcp.json"
```

---

## Task 3: README documentation

**File:** `README.md`

### Step 1: Update the Components section

Find the `## Components` section. Add a fourth bullet describing nginx:

After the existing `mcp-server` bullet, add:

```markdown
- **nginx** — runs in the same VM as vLLM and vllm-agent. Provides a single
  exposed endpoint (`:8443`) with path-prefix routing: `/v1/*` → vLLM,
  `/agent/*` → vllm-agent. Both backends bind to `127.0.0.1` only, so nginx
  is the only externally-reachable point of the VM.
```

### Step 2: Update the Provisioning section

Find the bullet that says "Installs vLLM and serves it on port 8000" — change to reflect the unified port:

```markdown
1. Creates an LXD VM with GPU passthrough.
2. Installs vLLM, vllm-agent, and nginx. vLLM and vllm-agent bind to 127.0.0.1
   inside the VM; nginx exposes them via path-prefix routing on a single
   external port (8443).
3. Updates `.mcp.json` with the unified URL: `VLLM_BASE_URL=http://<VM>:8443`
   and `VLLM_AGENT_URL=http://<VM>:8443/agent`.
```

### Step 3: Add a "URL layout" section right after MCP tools

Insert a new section before "Mode selection guidance":

```markdown
## URL layout (post-Plan-E)

The VM exposes a single port (`8443`) running nginx. Path prefixes route to
the right backend:

| External URL                    | Backend                  |
|---------------------------------|--------------------------|
| `http://VM:8443/v1/...`         | vLLM (OpenAI-compatible) |
| `http://VM:8443/health`         | vLLM liveness probe      |
| `http://VM:8443/agent/run`      | vllm-agent /run          |
| `http://VM:8443/agent/session/*`| vllm-agent /session/*    |
| `http://VM:8443/agent/skills`   | vllm-agent /skills       |
| `http://VM:8443/agent/artifacts`| vllm-agent /artifacts    |

The Authorization header (Bearer token for vllm-agent) is forwarded through
nginx unchanged. TLS termination is currently on the roadmap as a future
plan; today the endpoint is HTTP. Restrict LAN access via firewall or VPN
if exposing the VM beyond a trusted network.
```

### Step 4: Commit

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add README.md
git commit -m "Plan E: README documents unified :8443/agent endpoint"
```

---

## Final verification (after all 3 commits)

### Step 1: 3 commits

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git log --oneline 8269eb2..HEAD
```
Expected: 3 commits with the messages from Tasks 1–3.

### Step 2: bash + yaml validate (already done in tasks; re-run for final pass)

```bash
bash -n launch-inference.sh && echo "bash OK"
python3 -c "
import yaml
with open('profiles/rtx-inference.yaml.tpl') as f:
    text = f.read()
for k,v in [('__VLLM_MODEL__','m'),('__PROFILE_NAME__','p'),('__VLLM_MAX_LEN__','32768'),
            ('__VLLM_GPU_UTIL__','0.9'),('__VLLM_QUANT__','awq'),('__VLLM_API_KEY_ARG__',''),
            ('__BRIDGE__','br'),('__STORAGE_POOL__','pool'),('__ROOT_SIZE__','100GiB'),
            ('__LIMITS_CPU__','4'),('__LIMITS_MEMORY__','32GiB'),('__DDG_MIN_INTERVAL__','1.5'),
            ('__VLLM_AGENT_API_KEY__','testkey')]:
    text = text.replace(k,v)
yaml.safe_load(text); print('yaml OK')
"
```

### Step 3: vllm-agent unit tests still pass (no Python changes; should be unchanged)

```bash
cd vllm-agent && uv run pytest -v 2>&1 | tail -3
```
Expected: 79 PASS, 1 deselected (no regression).

### Step 4: live dogfood (optional)

```bash
KEY=$(cat /tmp/vllm-agent-key.txt)  # the key from Plan D
./launch-inference.sh --lxd-host bdx@192.168.7.11 --agent-api-key "$KEY"
```

After the script returns, validate:
- `cat .mcp.json | jq .` shows `:8443` and `/agent` in the URLs
- `curl http://<vm>:8443/health` → 200 (vLLM)
- `curl http://<vm>:8443/v1/models` → 200, lists the model
- `curl http://<vm>:8443/agent/run` (no auth) → **401**
- `curl -H "Authorization: Bearer $KEY" http://<vm>:8443/agent/run -X POST -d '{"task":"x","mode":"remote"}'` → **200** or 422 (not 401)
- `curl http://<vm>:8000/v1/models` (direct, bypassing nginx) → **connection refused** (vLLM bound to 127.0.0.1)
- `curl http://<vm>:8088/health` (direct, bypassing nginx) → **connection refused** (vllm-agent bound to 127.0.0.1)

Restart Claude Code to pick up the new `.mcp.json` env vars; `agent_run` and `agent_run_artifacts` should work transparently through the unified endpoint.

---

## Self-Review

### Spec coverage

| Goal | Tasks |
|---|---|
| nginx in VM, listening on `:8443` | Task 1 (steps 1, 4) |
| Path routing `/v1/*` → vLLM | Task 1 (step 4) |
| Path routing `/agent/*` → vllm-agent (prefix-stripped) | Task 1 (step 4) |
| `/health` → vLLM liveness | Task 1 (step 4) |
| Backends bind to 127.0.0.1 (enforce single-port) | Task 1 (steps 2, 3) |
| `.mcp.json` carries the unified URLs | Task 2 |
| Documentation | Task 3 |

### Placeholder scan

None. Every step has full code or exact commands.

### Type consistency

- `VLLM_BASE_URL` and `VLLM_AGENT_URL` env-var names unchanged across mcp-server, vllm-agent, launch script. Only their VALUES change in `.mcp.json`.
- nginx config uses port `8443` consistently.
- `proxy_pass http://127.0.0.1:8000;` (no trailing slash, vLLM keeps full path) vs `proxy_pass http://127.0.0.1:8088/;` (trailing slash, strips `/agent` prefix) — matches the documented routing intent.

### Risks

- **TLS not in scope.** HTTP only on `:8443`. If exposing beyond a trusted LAN, plan F should add self-signed or Let's Encrypt cert.
- **systemd order:** nginx may try to start before vLLM/vllm-agent are listening. Not fatal because nginx doesn't probe upstreams at startup — it'll proxy when first request arrives, by which time backends are typically up. If race becomes a problem, add `After=vllm.service vllm-agent.service` to nginx's drop-in.
- **No probe for nginx in launch-inference.sh.** The script probes vLLM and vllm-agent directly via lxc-exec to 127.0.0.1, which still works. We don't add an nginx-via-LAN probe; the smoke test at the end exercises vLLM through the original port path which now goes through nginx implicitly. Could add `curl http://<vm_ip>:8443/health` as an extra step, but YAGNI for now.

No blocking issues found.
