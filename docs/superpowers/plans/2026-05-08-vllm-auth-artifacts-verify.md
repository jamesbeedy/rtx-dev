# Plan D: Auth + Artifact Fetch + Verify Discipline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three quality-of-life improvements to the vllm/MCP integration:
1. **Static API key** auth on the vllm-agent HTTP server (closes the unauth-on-LAN hole)
2. **`agent_run_artifacts`** MCP tool so Claude can read `summary.md` / `files_changed.txt` / transcript tail back from completed runs (especially `mode=remote` runs whose artifacts live on the VM)
3. **Verify-before-finish** worker discipline so the model runs syntax/import/test checks before declaring done — would have caught the sort-viz "claimed all 6 algos, only 3 worked" failure

**Architecture:** Server-side adds a FastAPI dependency that checks `Authorization: Bearer <key>`; the MCP shim threads the same key on all httpx calls. A new `GET /artifacts?out_dir=...` endpoint reads files from disk and returns JSON; an `agent_run_artifacts(out_dir, mode)` MCP tool wraps it. The system prompt in `prompts.py` gains a verify-before-finish section that instructs the model to run `node --check` / `python -m py_compile` / tests via bash before calling `finish()`.

**Tech Stack:** FastAPI dependency injection, `httpx` Authorization header, env-var threading through `launch-inference.sh` + `rtx-inference.yaml.tpl`, prompt-string augmentation, pytest.

**Source review:** Plan B's per-batch reviews flagged "no auth on /run + LAN-bound" and "remote-mode artifacts unreachable from Claude"; the sort-viz dogfood test exposed "worker summary lied about implemented features."

---

## File Structure

```
rtx_5090_dev/
├── vllm-agent/
│   ├── src/vllm_agent/
│   │   ├── server.py                  # MODIFY: + auth dependency, + /artifacts endpoint
│   │   ├── prompts.py                 # MODIFY: + verify-before-finish discipline in _WORKER_TAIL
│   │   └── api.py                     # MODIFY: agent_run accepts/forwards api_key for client-side calls
│   └── tests/
│       ├── test_server.py             # MODIFY: + auth tests, + /artifacts tests
│       └── test_prompts.py            # MODIFY: + verify discipline assertion
├── mcp-server/
│   └── vllm_mcp.py                    # MODIFY: + VLLM_AGENT_API_KEY threading, + agent_run_artifacts tool
├── profiles/
│   └── rtx-inference.yaml.tpl         # MODIFY: + Environment=VLLM_AGENT_API_KEY=__VLLM_AGENT_API_KEY__
└── launch-inference.sh                 # MODIFY: + --agent-api-key flag, threading + .mcp.json wire-up
```

No new files.

---

## Task 1: Server-side API key + tests

**Issue:** vllm-agent's HTTP endpoints are unauthenticated. Anyone on the LAN can POST `/run` and run arbitrary bash on the VM.

**Files:**
- Modify: `vllm-agent/src/vllm_agent/server.py`
- Modify: `vllm-agent/tests/test_server.py`

- [ ] **Step 1: Add failing tests**

Append to `vllm-agent/tests/test_server.py`:

```python
def test_run_401_when_key_set_and_missing(client, tmp_path, monkeypatch):
    """When VLLM_AGENT_API_KEY is set, /run requires Bearer header."""
    monkeypatch.setenv("VLLM_AGENT_API_KEY", "sekret")
    # Reload the server module to pick up the env var. (The dependency reads
    # the env at import time; we re-import here for the test.)
    import importlib
    import vllm_agent.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    c = TestClient(srv.app)
    r = c.post("/run", json={"task": "x", "workdir": str(tmp_path)})
    assert r.status_code == 401


def test_run_401_when_key_wrong(tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_API_KEY", "sekret")
    import importlib
    import vllm_agent.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    c = TestClient(srv.app)
    r = c.post("/run", json={"task": "x", "workdir": str(tmp_path)},
               headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_health_open_even_when_key_set(monkeypatch):
    """`/health` is intentionally unauthenticated so probes can reach it."""
    monkeypatch.setenv("VLLM_AGENT_API_KEY", "sekret")
    import importlib
    import vllm_agent.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    c = TestClient(srv.app)
    r = c.get("/health")
    assert r.status_code == 200
```

Note: there's already a `test_health(client)` that uses the module-level `client` fixture (no auth set). Don't break it — these new tests use their own TestClient instances after reloading the module to flip the env var.

- [ ] **Step 2: Verify failures**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/vllm-agent && uv run pytest tests/test_server.py -v
```
Expected: 3 new FAILs (no auth gate yet — all return 200/422).

- [ ] **Step 3: Add the auth dependency to `server.py`**

In `vllm-agent/src/vllm_agent/server.py`, add to imports:

```python
from fastapi import Depends, Header
```

After the imports section (just before `app = FastAPI(...)`), add:

```python
VLLM_AGENT_API_KEY = os.environ.get("VLLM_AGENT_API_KEY", "")


async def require_key(authorization: str | None = Header(None)) -> None:
    """When VLLM_AGENT_API_KEY is set, require `Authorization: Bearer <key>`."""
    if not VLLM_AGENT_API_KEY:
        return  # auth disabled
    expected = f"Bearer {VLLM_AGENT_API_KEY}"
    if authorization != expected:
        raise HTTPException(401, "invalid or missing API key")
```

Then attach the dependency to every endpoint EXCEPT `/health`. Modify each decorator (the four POST routes and the two GET routes) to take `dependencies=[Depends(require_key)]`. Concretely:

- `@app.post("/run")` → `@app.post("/run", dependencies=[Depends(require_key)])`
- `@app.post("/session")` → `@app.post("/session", dependencies=[Depends(require_key)])`
- `@app.post("/session/{session_id}/step")` → `@app.post("/session/{session_id}/step", dependencies=[Depends(require_key)])`
- `@app.get("/session/{session_id}")` → `@app.get("/session/{session_id}", dependencies=[Depends(require_key)])`
- `@app.post("/session/{session_id}/stop")` → `@app.post("/session/{session_id}/stop", dependencies=[Depends(require_key)])`
- `@app.get("/skills")` → `@app.get("/skills", dependencies=[Depends(require_key)])`

Leave `@app.get("/health")` alone — orchestration probes need it.

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_server.py -v
```
Expected: all PASS, including the 3 new auth tests + the existing 6 server tests = 9 PASS in `test_server.py`. Full suite: ~74 PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add vllm-agent/src/vllm_agent/server.py vllm-agent/tests/test_server.py
git commit -m "Plan D: vllm-agent /run /session /skills require Bearer key when set"
```

---

## Task 2: Client-side API key threading in MCP shim

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

- [ ] **Step 1: Add the constant + helper**

Near the existing `VLLM_AGENT_URL` line (top of `vllm_mcp.py`), add:

```python
VLLM_AGENT_API_KEY = os.environ.get("VLLM_AGENT_API_KEY", "")


def _agent_headers() -> dict[str, str]:
    """Authorization header for VM-side vllm-agent calls. Empty if no key set."""
    if VLLM_AGENT_API_KEY:
        return {"Authorization": f"Bearer {VLLM_AGENT_API_KEY}"}
    return {}
```

- [ ] **Step 2: Apply the headers to every httpx call hitting VLLM_AGENT_URL**

In `_agent_run_remote(req)`:
```python
    async with httpx.AsyncClient(timeout=float(req.timeout_s + 30)) as client:
        r = await client.post(f"{VLLM_AGENT_URL}/run", json=body,
                              headers=_agent_headers())
```

Same pattern for the 4 `_http_session_*` helpers — each `client.post(...)` or `client.get(...)` gains `headers=_agent_headers()`.

5 sites total to update: `_agent_run_remote`, `_http_session_start`, `_http_session_step`, `_http_session_status`, `_http_session_stop`.

- [ ] **Step 3: Verify**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python3 -c "import vllm_mcp; print('imports OK')"
```

- [ ] **Step 4: Smoke test against current VM (no key set yet — should still work)**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && \
  VLLM_BASE_URL=http://192.168.9.154:8000 \
  VLLM_MODEL=QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ \
  VLLM_AGENT_URL=http://192.168.9.154:8088 \
  .venv/bin/python -c "
import asyncio
from vllm_mcp import agent_run
async def main():
    out = await agent_run(task='Call finish() with summary \"key-threading smoke\"',
                          mode='remote', workdir='/tmp',
                          out_dir='/tmp/agent_run_d2_smoke',
                          max_iterations=3, max_tokens=128, temperature=0)
    print(out['status'], out['error'])
asyncio.run(main())
"
```
Expected: `ok None`. The VM doesn't have `VLLM_AGENT_API_KEY` set yet (default empty), so `_agent_headers()` returns `{}` and the call succeeds — confirming backward compat.

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan D: MCP shim threads VLLM_AGENT_API_KEY into Authorization header"
```

---

## Task 3: Provisioning — `--agent-api-key` flag + template wiring

**Files:**
- Modify: `launch-inference.sh`
- Modify: `profiles/rtx-inference.yaml.tpl`

- [ ] **Step 1: Add the CLI flag to `launch-inference.sh`**

In the defaults block at the top of `launch-inference.sh` (alongside `API_KEY=""` for vLLM), add:

```bash
AGENT_API_KEY=""    # optional auth for vllm-agent serve (Bearer); default: no auth
```

In the `usage()` heredoc, add a line under the existing `--api-key SECRET` description:

```
  --agent-api-key SECRET      Enable Bearer auth on vllm-agent serve.
                              (Default: no auth — only safe on a trusted LAN.)
```

In the argument-parsing case statement (alongside `--api-key)`):

```bash
    --agent-api-key)  AGENT_API_KEY="$2"; shift 2;;
```

- [ ] **Step 2: Thread the value into the template substitution**

Find the `sed` pipeline that renders the template. Add ONE more `-e` line (consistent with the other `__TOKEN__|$VAR` substitutions):

```bash
  -e "s|__VLLM_AGENT_API_KEY__|$AGENT_API_KEY|g" \
```

- [ ] **Step 3: Update `.mcp.json` writer to include the key**

Find the `python3 -` heredoc that updates `.mcp.json` (inside the `if [[ -f "$MCP_JSON" ]]` block). It currently passes 6 positional args (path, base_url, model, ddg_interval, api_key, agent_url). Add a 7th: `"$AGENT_API_KEY"`.

Update the `python3` invocation:
```bash
python3 - "$MCP_JSON" "http://${VM_IP}:${PORT}" "$MODEL" "$DDG_INTERVAL" "$API_KEY" "http://${VM_IP}:8088" "$AGENT_API_KEY" <<'PYEOF'
```

Inside the heredoc, change:
```python
path, base_url, model, ddg_interval, api_key, agent_url = sys.argv[1:7]
```
to:
```python
path, base_url, model, ddg_interval, api_key, agent_url, agent_api_key = sys.argv[1:8]
```

After `env["VLLM_AGENT_URL"] = agent_url`, add:
```python
if agent_api_key:
    env["VLLM_AGENT_API_KEY"] = agent_api_key
elif "VLLM_AGENT_API_KEY" in env:
    del env["VLLM_AGENT_API_KEY"]
```

- [ ] **Step 4: Wire into the cloud-init template**

In `profiles/rtx-inference.yaml.tpl`, find the `vllm-agent.service` `[Service]` section. After `Environment=DDG_MIN_INTERVAL_S=__DDG_MIN_INTERVAL__`, add:

```
        Environment=VLLM_AGENT_API_KEY=__VLLM_AGENT_API_KEY__
```

(Match the existing `Environment=` lines' indentation. With no `--agent-api-key` provided, the `__VLLM_AGENT_API_KEY__` substitution becomes the empty string, which the server reads as "auth disabled" — backward compatible.)

- [ ] **Step 5: Update the example MCP config printed at the end of the script**

Find the `cat <<EOF` block at the end that prints a sample `mcpServers` config. Update the env block to conditionally show `VLLM_AGENT_API_KEY`:

Before the `cat <<EOF`, compute the optional line:
```bash
AGENT_KEY_LINE=""
if [[ -n "$AGENT_API_KEY" ]]; then
  AGENT_KEY_LINE=$',\n          "VLLM_AGENT_API_KEY": "'"$AGENT_API_KEY"'"'
fi
```

Then in the heredoc, add `$AGENT_KEY_LINE` after the existing `"VLLM_AGENT_URL"` line (similar pattern to the existing `VLLM_API_KEY` conditional inclusion).

- [ ] **Step 6: Validate**

YAML:
```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev && python3 -c "
import yaml
with open('profiles/rtx-inference.yaml.tpl') as f:
    text = f.read()
for k,v in [('__VLLM_MODEL__','m'),('__PROFILE_NAME__','p'),('__VLLM_MAX_LEN__','32768'),
            ('__VLLM_GPU_UTIL__','0.9'),('__VLLM_QUANT__','awq'),('__VLLM_API_KEY_ARG__',''),
            ('__BRIDGE__','br'),('__STORAGE_POOL__','pool'),('__ROOT_SIZE__','100GiB'),
            ('__LIMITS_CPU__','4'),('__LIMITS_MEMORY__','32GiB'),('__DDG_MIN_INTERVAL__','1.5'),
            ('__VLLM_AGENT_API_KEY__','')]:
    text = text.replace(k,v)
yaml.safe_load(text); print('yaml OK')
"
```

Bash:
```bash
bash -n /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/launch-inference.sh && echo "bash OK"
```

- [ ] **Step 7: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add profiles/rtx-inference.yaml.tpl launch-inference.sh
git commit -m "Plan D: --agent-api-key flag threads VLLM_AGENT_API_KEY into VM systemd unit + .mcp.json"
```

---

## Task 4: Server-side `/artifacts` endpoint

**Issue:** After a `mode=remote` run completes, Claude has paths into the VM's filesystem (`/tmp/agent_run_*/summary.md`, `/home/ubuntu/.cache/vllm-agent/sessions/<id>/...`) but no way to read them. Add an HTTP endpoint that reads back the standard artifacts.

**Files:**
- Modify: `vllm-agent/src/vllm_agent/server.py`
- Modify: `vllm-agent/tests/test_server.py`

- [ ] **Step 1: Add failing tests**

Append to `vllm-agent/tests/test_server.py`:

```python
def test_artifacts_returns_summary_files_changed_transcript_tail(client, tmp_path):
    # Set up an artifact dir as agent_run would write it.
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.md").write_text("did the thing\n")
    (out / "files_changed.txt").write_text("a.py\nb.py\n")
    (out / "transcript.jsonl").write_text(
        '{"kind":"message","role":"system","content":"sys"}\n'
        '{"kind":"message","role":"user","content":"go"}\n'
        '{"kind":"tool_call","tool":"finish","args":{},"result":{"status":"finished"}}\n'
    )
    r = client.get(f"/artifacts?out_dir={out}")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == "did the thing\n"
    assert body["files_changed"] == ["a.py", "b.py"]
    assert len(body["transcript_tail"]) == 3
    assert body["transcript_tail"][-1]["kind"] == "tool_call"


def test_artifacts_404_when_dir_missing(client, tmp_path):
    r = client.get(f"/artifacts?out_dir={tmp_path}/nonexistent")
    assert r.status_code == 404


def test_artifacts_tail_lines_caps_transcript(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.md").write_text("ok")
    (out / "files_changed.txt").write_text("")
    (out / "transcript.jsonl").write_text(
        "\n".join(f'{{"kind":"message","i":{i}}}' for i in range(50)) + "\n"
    )
    r = client.get(f"/artifacts?out_dir={out}&tail_lines=10")
    assert r.status_code == 200
    assert len(r.json()["transcript_tail"]) == 10
    # Must be the LAST 10, not the first.
    assert r.json()["transcript_tail"][0]["i"] == 40
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Implement the endpoint**

In `vllm-agent/src/vllm_agent/server.py`, add a new endpoint (place it near `/skills`). Apply `Depends(require_key)` like the other authenticated endpoints:

```python
@app.get("/artifacts", dependencies=[Depends(require_key)])
async def artifacts(out_dir: str, tail_lines: int = 50) -> dict:
    """Read back the standard artifacts of a completed run.

    Returns: {summary, files_changed, transcript_tail}.
    `transcript_tail` is the last `tail_lines` parsed JSONL records.
    """
    import json
    from pathlib import Path

    base = Path(out_dir).expanduser()
    if not base.is_dir():
        raise HTTPException(404, f"out_dir not found: {base}")

    summary = ""
    summary_p = base / "summary.md"
    if summary_p.exists():
        summary = summary_p.read_text()

    files_changed: list[str] = []
    fc_p = base / "files_changed.txt"
    if fc_p.exists():
        files_changed = [ln for ln in fc_p.read_text().splitlines() if ln.strip()]

    transcript_tail: list[dict] = []
    t_p = base / "transcript.jsonl"
    if t_p.exists():
        lines = [ln for ln in t_p.read_text().splitlines() if ln.strip()]
        for ln in lines[-tail_lines:]:
            try:
                transcript_tail.append(json.loads(ln))
            except json.JSONDecodeError:
                continue

    return {
        "out_dir": str(base),
        "summary": summary,
        "files_changed": files_changed,
        "transcript_tail": transcript_tail,
    }
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_server.py -v
```
Expected: all PASS, +3 new artifacts tests.

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add vllm-agent/src/vllm_agent/server.py vllm-agent/tests/test_server.py
git commit -m "Plan D: GET /artifacts returns summary + files_changed + transcript tail"
```

---

## Task 5: MCP shim `agent_run_artifacts` tool

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

- [ ] **Step 1: Add the tool**

In `mcp-server/vllm_mcp.py`, near the existing agent dispatch tools (after `agent_session_stop`), add:

```python
@mcp.tool()
async def agent_run_artifacts(
    out_dir: str,
    mode: str = "remote",
    tail_lines: int = 50,
) -> dict[str, Any]:
    """Read back the artifacts (summary.md, files_changed.txt, transcript tail)
    of a completed agent run.

    For mode='local' the artifacts live on the user's machine and Claude can
    Read() them directly — but this tool gives a convenient unified shape.

    For mode='remote' the artifacts live on the VM; this tool fetches them
    via the vllm-agent HTTP API.

    Returns: {out_dir, summary, files_changed, transcript_tail}.
    """
    if mode == "local":
        # Read from local disk directly — same shape the server returns.
        from pathlib import Path
        base = Path(out_dir).expanduser()
        if not base.is_dir():
            return {"error": f"out_dir not found: {base}"}
        summary = ""
        summary_p = base / "summary.md"
        if summary_p.exists():
            summary = summary_p.read_text()
        files_changed: list[str] = []
        fc_p = base / "files_changed.txt"
        if fc_p.exists():
            files_changed = [ln for ln in fc_p.read_text().splitlines() if ln.strip()]
        transcript_tail: list[dict] = []
        t_p = base / "transcript.jsonl"
        if t_p.exists():
            lines = [ln for ln in t_p.read_text().splitlines() if ln.strip()]
            for ln in lines[-tail_lines:]:
                try:
                    transcript_tail.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        return {
            "out_dir": str(base),
            "summary": summary,
            "files_changed": files_changed,
            "transcript_tail": transcript_tail,
        }
    # mode == "remote"
    if not VLLM_AGENT_URL:
        return {"error": "VLLM_AGENT_URL not set; cannot use mode=remote"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{VLLM_AGENT_URL}/artifacts",
            params={"out_dir": out_dir, "tail_lines": tail_lines},
            headers=_agent_headers(),
        )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    return r.json()
```

- [ ] **Step 2: Verify imports + tool registration**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python3 -c "
import vllm_mcp
import inspect
funcs = [n for n,o in inspect.getmembers(vllm_mcp) if inspect.iscoroutinefunction(o) and not n.startswith('_')]
print(f'count: {len(funcs)}')
assert 'agent_run_artifacts' in funcs
print('OK')
"
```
Expected: `count: 13` (was 12 async + 1 sync; now 13 async + 1 sync = 14 total registered; the count from the inspect filter goes from 12 to 13). Adjust your assertion based on what the previous count was.

- [ ] **Step 3: End-to-end smoke test**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && \
  VLLM_BASE_URL=http://192.168.9.154:8000 \
  VLLM_MODEL=QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ \
  VLLM_AGENT_URL=http://192.168.9.154:8088 \
  .venv/bin/python -c "
import asyncio, json
from vllm_mcp import agent_run, agent_run_artifacts
async def main():
    r = await agent_run(task='Call finish() with summary \"artifact roundtrip ok\"',
                        mode='remote', workdir='/tmp',
                        out_dir='/tmp/agent_run_d5_smoke',
                        max_iterations=3, max_tokens=128, temperature=0)
    print('run status:', r['status'])
    a = await agent_run_artifacts(out_dir=r['out_dir'], mode='remote')
    print('summary:', a.get('summary'))
    print('files_changed:', a.get('files_changed'))
    print('transcript_tail count:', len(a.get('transcript_tail', [])))
asyncio.run(main())
"
```
Expected: `summary: artifact roundtrip ok`, transcript_tail with several records.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan D: agent_run_artifacts MCP tool reads summary/files_changed/transcript"
```

---

## Task 6: Verify-before-finish worker discipline

**Issue:** The 30B worker produces façade-y first drafts and its `finish()` summaries are aspirational (the sort-viz Pass 1 claimed all 6 algos worked; merge/quick/heap were stubs). Adding explicit "verify your work via bash before finish()" to the system prompt nudges the model toward actual self-verification.

**Files:**
- Modify: `vllm-agent/src/vllm_agent/prompts.py`
- Modify: `vllm-agent/tests/test_prompts.py`

- [ ] **Step 1: Add a failing test**

Append to `vllm-agent/tests/test_prompts.py`:

```python
def test_system_prompt_includes_verify_discipline():
    out = build_system_prompt(skill_content=None, workdir="/tmp", mode="remote")
    # Worker is told to verify before finish, with concrete commands.
    assert "VERIFY" in out or "verify" in out.lower()
    assert "node --check" in out or "py_compile" in out or "bash -n" in out
    # Summary discipline: factual, not aspirational.
    assert "FACTUAL" in out or "factual" in out.lower() or "actually" in out.lower()
```

- [ ] **Step 2: Verify failure**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/vllm-agent && uv run pytest tests/test_prompts.py::test_system_prompt_includes_verify_discipline -v
```
Expected: FAIL.

- [ ] **Step 3: Augment `_WORKER_TAIL` in `prompts.py`**

In `vllm-agent/src/vllm_agent/prompts.py`, replace the existing `_WORKER_TAIL` constant with:

```python
_WORKER_TAIL = """
You are the vllm-rtx5090 worker. You have these tools:
  read_file, write_file, edit_file, bash, grep, glob, web_search, finish

Workspace: {workdir}
Mode: {mode}

Discipline:
- Edit files in place via edit_file/write_file. Run tests via bash.
- Iterate until the task is done or you hit a blocker.
- Never edit files outside {workdir}.
- Use web_search for facts you don't reliably know.

Verify-before-finish:
- BEFORE calling finish(), VERIFY the work you claim to have done. Use bash:
    * Python files:   `python3 -m py_compile <file>` or `python3 -c "import <m>"`
    * Node/JS files:  `node --check <file>`
    * Shell scripts:  `bash -n <file>`
    * YAML configs:   `python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" <file>`
    * Tests:          run them with the project's test runner; require zero failures.
  If bash is unavailable in your environment, skip verification but be honest
  about it in the summary.
- If verification fails, FIX the issue and re-verify before calling finish().
  Do NOT call finish() with a known-broken result.

Summary discipline:
- The summary you pass to finish() must be FACTUAL, not aspirational. Only list
  what you ACTUALLY verified, not what you intended. If a feature is partial
  or stubbed, say so explicitly. If verification was skipped, say WHY.
- When done, call finish() with a 1-2 paragraph summary of what you did,
  what you changed, what you verified, and anything the orchestrator should
  double-check.
"""
```

The structural change: existing single "Discipline" block is split into three: behavior discipline, verify-before-finish discipline, summary discipline.

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_prompts.py -v
```
Expected: all PASS, including the new verify-discipline test.

Also run the full suite to confirm no regression (the existing `test_build_system_prompt_no_skill` and `test_build_system_prompt_with_skill` rely on substrings that may overlap with the new text):

```bash
cd vllm-agent && uv run pytest -v
```
Expected: all PASS. If the existing prompt tests fail because the prompt got longer, the assertions there are substring-checks (`"vllm-rtx5090 worker" in out`, `"Mode: remote" in out`, `"the full skill body" in out`) — these should still hold. Verify.

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add vllm-agent/src/vllm_agent/prompts.py vllm-agent/tests/test_prompts.py
git commit -m "Plan D: worker prompt — verify-before-finish + factual-summary discipline"
```

---

## Final verification

- [ ] **Step 1: 6 commits**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git log --oneline 92fefb6..HEAD
```
Expected: 6 commits with messages from Tasks 1–6.

- [ ] **Step 2: Full test suite**

```bash
cd vllm-agent && uv run pytest -v
```
Expected: ~78 tests PASS (71 baseline + ~7 new across tasks 1, 4, 6).

- [ ] **Step 3: MCP tool count**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python3 -c "
import vllm_mcp, inspect
funcs = sorted(n for n,o in inspect.getmembers(vllm_mcp) if inspect.iscoroutinefunction(o) and not n.startswith('_'))
print(f'{len(funcs)} async tools: {funcs}')
"
```
Expected: 13 async tools (was 12; +1 = `agent_run_artifacts`). Plus the existing sync `verify_project` = 14 registered MCP tools total.

- [ ] **Step 4: Bash + YAML validate**

```bash
bash -n /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/launch-inference.sh && echo "bash OK"
```

- [ ] **Step 5: Live dogfood — re-provision with `--agent-api-key`**

(OPTIONAL — only if you want to validate the auth path against a real VM. Otherwise skip; the unit tests cover correctness.)

```bash
KEY=$(openssl rand -hex 32) && \
./launch-inference.sh --lxd-host bdx@192.168.7.11 --agent-api-key "$KEY"
```

After the script returns, verify:
- `cat .mcp.json` shows `"VLLM_AGENT_API_KEY": "<key>"` in the env block
- `curl http://<vm_ip>:8088/run -X POST -H 'Content-Type: application/json' -d '{}' ` returns 401
- `curl http://<vm_ip>:8088/run -X POST -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{"task":"x","mode":"remote"}'` returns a non-401 (probably 422 or 200)
- `curl http://<vm_ip>:8088/health` returns 200 with no auth (probes still work)

Restart Claude Code to pick up the new env vars in the MCP shim, then `agent_run` should work transparently.

---

## Self-Review

### Spec coverage

| Improvement | Tasks |
|---|---|
| Static API key (server) | Task 1 |
| Static API key (client) | Task 2 |
| Static API key (provisioning) | Task 3 |
| `agent_run_artifacts` (server endpoint) | Task 4 |
| `agent_run_artifacts` (MCP tool) | Task 5 |
| Verify-before-finish discipline | Task 6 |

### Placeholder scan
None. Every step has full code or exact commands.

### Type consistency
- `VLLM_AGENT_API_KEY` env-var name consistent across server, shim, template, launch script.
- `_agent_headers()` helper used by all 5 remote-dispatch sites.
- `agent_run_artifacts` return shape matches `/artifacts` endpoint shape (out_dir, summary, files_changed, transcript_tail).
- The new prompt's verify-discipline block uses bash commands (`node --check`, `python3 -m py_compile`, `bash -n`) consistent with the worker's bash tool semantics.

No issues found.
