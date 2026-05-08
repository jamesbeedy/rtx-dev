# vllm-agent MCP Integration + VM Provisioning — Implementation Plan (Plan B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the standalone `vllm-agent` runtime (built in Plan A) into the existing `mcp-server` so Claude Code gets new MCP tools (`agent_run`, `agent_session_*`, `list_skills`), apply the 4 follow-ups from Plan A's final review, refactor the existing MCP tools (`ask`/`converse`/`critique`/`scaffold`) to delegate to `vllm_agent.loop`, install `vllm-agent serve` in the GPU VM via cloud-init, and update README + add CLAUDE.md.

**Architecture:** The MCP shim becomes a thin layer that imports `vllm_agent.api` (for `mode=local`) or POSTs to a VM-side `vllm-agent serve` HTTP API (for `mode=remote`). Existing MCP tools stop owning their own tool-call loop and reuse `vllm_agent.loop.run_loop` with a restricted tool palette. The VM gets a systemd unit running `vllm-agent serve` next to vLLM.

**Tech Stack:** Python 3.11+ (mcp-server), `httpx` (remote dispatch), `mcp>=1.2.0` (already installed), uv (package manager), systemd (VM service), cloud-init (VM bootstrap).

**Source spec:** `docs/superpowers/specs/2026-05-08-vllm-heavy-lifting-design.md`
**Plan A:** `docs/superpowers/plans/2026-05-08-vllm-agent-runtime.md` (already executed; 24 commits in `15061e1..2703c24`)

---

## File Structure

```
rtx_5090_dev/
├── vllm-agent/                                    # Existing (Plan A)
│   └── src/vllm_agent/
│       ├── api.py                                 # MODIFY: + timeout, + search_log
│       ├── loop.py                                # MODIFY: + tools_subset arg
│       ├── sessions.py                            # MODIFY: + session_dir public
│       └── server.py                              # MODIFY: + 404 handling
├── mcp-server/
│   ├── pyproject.toml                             # MODIFY: + vllm-agent dep
│   └── vllm_mcp.py                                # MODIFY: refactor + new tools
├── profiles/
│   └── rtx-inference.yaml.tpl                     # MODIFY: + vllm-agent install + systemd
├── launch-inference.sh                             # MODIFY: + port export + .mcp.json wiring
├── README.md                                       # MODIFY: document new tool surface
└── CLAUDE.md                                       # CREATE: routing guidance
```

No new packages are introduced. The MCP shim grows in scope but the existing 7 tools (`health`, `list_models`, `verify_project`, `ask`, `converse`, `critique`, `scaffold`) keep working.

---

## Phase 1 — vllm-agent follow-ups (4 fixes from Plan A's final review)

Skipped from Plan A's final review by design (non-blocking). Apply now before the MCP shim depends on the contracts.

---

## Task 1: Add `timeout` status to `agent_run`

**Issue (from Plan A review #1):** `agent_run.status` doesn't include `"timeout"`. The design spec promises `"ok" | "max_iterations" | "timeout" | "error"`. Currently a per-request httpx timeout surfaces as `"error"`.

**Files:**
- Modify: `vllm-agent/src/vllm_agent/api.py`
- Modify: `vllm-agent/tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Append to `vllm-agent/tests/test_api.py`:

```python
@respx.mock
async def test_agent_run_emits_timeout_status(tmp_path, monkeypatch):
    """If the run exceeds timeout_s, status is 'timeout'."""
    # vLLM responds slowly enough to bust the agent_run timeout (not the httpx one).
    import asyncio as _asyncio

    async def _slow(_request):
        await _asyncio.sleep(2.0)
        return Response(200, json={"choices": [{"message": {"content": "late"}}]})

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_slow)
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    req = AgentRunRequest(
        task="anything",
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        max_iterations=1,
        timeout_s=1,   # 1-second wall clock
    )
    result = await agent_run(req)
    assert result.status == "timeout"
    assert result.error and "timeout" in result.error.lower()
```

- [ ] **Step 2: Verify it fails**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/vllm-agent && uv run pytest tests/test_api.py::test_agent_run_emits_timeout_status -v
```
Expected: FAIL — current code returns `status="error"` when wall-clock is exceeded (or doesn't enforce a wall-clock at all and just lets the httpx timeout fire).

- [ ] **Step 3: Wrap `run_loop` in `asyncio.wait_for`**

In `vllm-agent/src/vllm_agent/api.py`, replace this block (around the existing `t0 = time.perf_counter()` / `loop_result = await run_loop(...)` lines inside `agent_run`):

```python
    t0 = time.perf_counter()
    loop_result = await run_loop(msgs, ctx, cfg)
    duration = time.perf_counter() - t0
```

with:

```python
    import asyncio
    t0 = time.perf_counter()
    try:
        loop_result = await asyncio.wait_for(run_loop(msgs, ctx, cfg),
                                             timeout=float(req.timeout_s))
    except asyncio.TimeoutError:
        duration = time.perf_counter() - t0
        files_changed = _files_changed(before, ws.root)
        (out_dir / "files_changed.txt").write_text(
            "\n".join(files_changed) + ("\n" if files_changed else ""))
        summary_path = out_dir / "summary.md"
        if not summary_path.exists():
            summary_path.write_text("(timed out before finish)")
        return AgentRunResult(
            run_id=run_id, out_dir=str(out_dir), summary_path=str(summary_path),
            files_changed=files_changed, diff_path=None,
            iterations=0, duration_s=round(duration, 2),
            status="timeout", error=f"agent_run exceeded timeout_s={req.timeout_s}",
        )
    duration = time.perf_counter() - t0
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd vllm-agent && uv run pytest tests/test_api.py -v
```
Expected: 3 PASS (existing 2 + 1 new).

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add vllm-agent/src/vllm_agent/api.py vllm-agent/tests/test_api.py
git commit -m "Plan B: agent_run emits status='timeout' on wall-clock breach"
```

---

## Task 2: Add `search_log` field to `AgentRunResult`

**Issue (from Plan A review #2):** Design spec lists `search_log` in the `agent_run` result; implementation doesn't have it.

**Files:**
- Modify: `vllm-agent/src/vllm_agent/api.py`
- Modify: `vllm-agent/tests/test_api.py`

- [ ] **Step 1: Add a failing test**

Append to `vllm-agent/tests/test_api.py`:

```python
@respx.mock
async def test_agent_run_populates_search_log(tmp_path, monkeypatch):
    """If the worker calls web_search during a run, search_log lists the queries."""
    seq = [
        # First reply: web_search
        _resp(tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "web_search",
                         "arguments": json.dumps({"query": "test query"})},
        }]),
        # Second reply: finish
        _resp(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "finish",
                         "arguments": json.dumps({"summary": "did a search"})},
        }]),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=seq[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    # Mock DDG too so the search succeeds.
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=Response(200, text="<html></html>"))

    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    req = AgentRunRequest(
        task="search for something",
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        max_iterations=3,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    assert len(result.search_log) == 1
    assert result.search_log[0]["query"] == "test query"
```

- [ ] **Step 2: Verify it fails**

```bash
cd vllm-agent && uv run pytest tests/test_api.py::test_agent_run_populates_search_log -v
```
Expected: FAIL — `AgentRunResult` has no `search_log` attr.

- [ ] **Step 3: Add `search_log` to `AgentRunResult` and populate it**

In `vllm-agent/src/vllm_agent/api.py`:

a) Add `search_log: list[dict] = None` to the `AgentRunResult` dataclass (use `field(default_factory=list)`):

Replace the dataclass definition with:
```python
@dataclass
class AgentRunResult:
    run_id: str
    out_dir: str
    summary_path: str
    files_changed: list[str]
    diff_path: str | None
    iterations: int
    duration_s: float
    status: str
    error: str | None = None
    search_log: list[dict] = field(default_factory=list)
```

(Add `field` to the existing `from dataclasses import dataclass` line: `from dataclasses import dataclass, field`.)

b) Populate it in `agent_run()`. After the `run_loop` call returns successfully, derive search_log from the transcript by scanning for tool_call records of `web_search`:

Add this helper near the bottom of the file (before the session API block):
```python
def _extract_search_log(transcript_path: Path) -> list[dict]:
    """Pull web_search records out of the transcript JSONL."""
    if not transcript_path.exists():
        return []
    out: list[dict] = []
    for line in transcript_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "tool_call" and rec.get("tool") == "web_search":
            args = rec.get("args") or {}
            result = rec.get("result") or {}
            out.append({
                "query": args.get("query", ""),
                "n_results": len(result.get("results", []) or []),
                "error": result.get("error"),
            })
    return out
```

(Add `import json` at the top of `api.py` if not already present — it likely is via the imports for transcript reading.)

c) Use it: replace the final `return AgentRunResult(...)` block (the success path, at the very bottom of `agent_run`) with one that includes `search_log=_extract_search_log(out_dir / "transcript.jsonl")`. Same for the timeout-return added in Task 1: include `search_log=_extract_search_log(...)` there too.

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_api.py -v
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/api.py vllm-agent/tests/test_api.py
git commit -m "Plan B: AgentRunResult.search_log populated from transcript"
```

---

## Task 3: Add 404 handling on `/session/{id}/step` and `/session/{id}/stop`; add `step_status` field

**Issues (from Plan A review #3 and #4):**
- `/session/{id}/step` and `/session/{id}/stop` raise 500 instead of 404 for unknown session_id.
- `agent_session_step.status` returns lifecycle vocab; design spec wants run-status vocab. Add a separate `step_status` field so callers see both.

**Files:**
- Modify: `vllm-agent/src/vllm_agent/api.py`
- Modify: `vllm-agent/src/vllm_agent/server.py`
- Modify: `vllm-agent/tests/test_api.py`
- Modify: `vllm-agent/tests/test_server.py`

- [ ] **Step 1: Add failing tests**

Append to `vllm-agent/tests/test_server.py`:

```python
def test_session_step_404_for_unknown(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))
    r = client.post("/session/does-not-exist/step", json={"max_iterations": 1})
    assert r.status_code == 404
    assert "does-not-exist" in r.json().get("detail", "")


def test_session_stop_404_for_unknown(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))
    r = client.post("/session/does-not-exist/stop")
    assert r.status_code == 404
```

Append to `vllm-agent/tests/test_api.py`:

```python
@respx.mock
async def test_agent_session_step_returns_step_status(tmp_path, monkeypatch):
    """agent_session_step returns BOTH lifecycle status and per-step run status."""
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "done", "tool_calls": []}}]
        })
    )
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))

    from vllm_agent.api import (
        agent_session_start, agent_session_step,
        AgentSessionStartRequest,
    )
    s = await agent_session_start(AgentSessionStartRequest(
        goal="g", workdir=str(tmp_path)))
    step = await agent_session_step(s.session_id, max_iterations=2)
    assert step.status == "completed"     # lifecycle
    assert step.step_status == "ok"       # run-status (no tool_calls → ok)
```

- [ ] **Step 2: Verify failures**

```bash
cd vllm-agent && uv run pytest tests/test_server.py tests/test_api.py -v
```
Expected: 3 FAILs (404 tests + step_status test).

- [ ] **Step 3a: Add `step_status` to `AgentSessionStepResult`**

In `vllm-agent/src/vllm_agent/api.py`, change `AgentSessionStepResult` to:

```python
@dataclass
class AgentSessionStepResult:
    session_id: str
    iterations_this_step: int
    files_changed_this_step: list[str]
    summary_path: str
    status: str          # SessionStatus value: "running"|"completed"|"errored"|"stopped"
    step_status: str     # raw loop status: "ok"|"max_iterations"|"error"
```

In `agent_session_step` near the bottom, change the final return to:
```python
    return AgentSessionStepResult(
        session_id=session_id,
        iterations_this_step=loop_result.iterations,
        files_changed_this_step=files_changed,
        summary_path=str(sess_dir / "summary.md"),
        status=store.load(session_id).status.value,
        step_status=loop_result.status,
    )
```

Also update the dead-status guard at the top of `agent_session_step` (the early-return path) to include `step_status`:
```python
        return AgentSessionStepResult(
            session_id=session_id, iterations_this_step=0,
            files_changed_this_step=[],
            summary_path=str(store._dir(session_id) / "summary.md"),
            status=s.status.value,
            step_status="not_started",
        )
```

- [ ] **Step 3b: Add 404 wrappers in `server.py`**

In `vllm-agent/src/vllm_agent/server.py`, change the `/session/{id}/step` endpoint to:

```python
@app.post("/session/{session_id}/step")
async def session_step(session_id: str, body: SessionStepBody) -> dict:
    try:
        result = await agent_session_step(
            session_id, nudge=body.nudge, max_iterations=body.max_iterations)
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")
    return asdict(result)
```

Change `/session/{id}/stop` to:

```python
@app.post("/session/{session_id}/stop")
async def session_stop(session_id: str) -> dict:
    try:
        result = await agent_session_stop(session_id)
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")
    return asdict(result)
```

`agent_session_stop` currently calls `store.set_status(session_id, ...)` which calls `store.load(session_id)` which raises KeyError. So the catch will fire. But the existing implementation of `agent_session_stop` doesn't wrap the call — let's also have it bubble the KeyError naturally (no change needed if `set_status` already raises through to the caller). If you find `agent_session_stop` swallows the error, fix it.

- [ ] **Step 3c: Update the existing `test_agent_session_start_step_status_stop` test in `test_api.py`** to assert the new `step_status` field too:

Find the `assert step.status == "completed"` line and add:
```python
    assert step.step_status == "ok"
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/ -v
```
Expected: 67 PASS (62 prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/api.py vllm-agent/src/vllm_agent/server.py vllm-agent/tests/test_api.py vllm-agent/tests/test_server.py
git commit -m "Plan B: session 404 handling + step_status field"
```

---

## Task 4: HTTP TestClient tests for the rest of `/session/*` endpoints

**Issue (from Plan A review #5):** `/session/{id}/step`, `/session/{id}/stop`, and `/session/{id}` (status) GET have no HTTP-layer tests.

**Files:**
- Modify: `vllm-agent/tests/test_server.py`

- [ ] **Step 1: Add failing tests**

Append to `vllm-agent/tests/test_server.py`:

```python
@respx.mock
def test_session_full_lifecycle(client, tmp_path, monkeypatch):
    """Start → step → status → stop, all via HTTP."""
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "ok", "tool_calls": []}}]
        })
    )
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))

    # Start
    r = client.post("/session", json={"goal": "g", "workdir": str(tmp_path)})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["status"] == "running"

    # Step
    r = client.post(f"/session/{sid}/step", json={"max_iterations": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["status"] == "completed"
    assert body["step_status"] == "ok"

    # Status
    r = client.get(f"/session/{sid}")
    assert r.status_code == 200
    assert r.json()["iterations_total"] == 1

    # Stop (idempotent on a completed session — should just set status to stopped)
    r = client.post(f"/session/{sid}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


def test_session_status_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))
    r = client.get("/session/does-not-exist")
    assert r.status_code == 404
```

- [ ] **Step 2: Verify failures**

```bash
cd vllm-agent && uv run pytest tests/test_server.py -v
```
Expected: 2 new FAILs only if the existing infrastructure doesn't already cover these paths. (404 on status was added in Plan A; that test should already pass.)

- [ ] **Step 3: No code changes needed (the endpoints exist; we're filling in test coverage)**

Run the suite again:
```bash
cd vllm-agent && uv run pytest tests/ -v
```
Expected: all PASS, 69 tests total.

- [ ] **Step 4: Commit**

```bash
git add vllm-agent/tests/test_server.py
git commit -m "Plan B: HTTP tests for session lifecycle endpoints"
```

---

## Phase 2 — vllm-agent additions for MCP integration

---

## Task 5: `tools_subset` parameter on `run_loop` + `session_dir` public method

**Why:** The MCP shim refactor (Phase 3) needs to call `run_loop` with only `web_search` available (matching existing `_generate()` semantics in `vllm_mcp.py`). The default of "all WORKER_TOOLS" is wrong for that use. Also expose `SessionStore.session_dir(id)` so `api.py` stops reaching into `_dir`.

**Files:**
- Modify: `vllm-agent/src/vllm_agent/loop.py`
- Modify: `vllm-agent/src/vllm_agent/sessions.py`
- Modify: `vllm-agent/src/vllm_agent/api.py`
- Modify: `vllm-agent/tests/test_loop.py`

- [ ] **Step 1: Add failing test**

Append to `vllm-agent/tests/test_loop.py`:

```python
@respx.mock
async def test_loop_tools_subset_restricts_palette(tmp_path):
    """When tools_subset is set, only those tools are advertised to vLLM."""
    captured = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json=_vllm_response(content="done"))

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_capture)

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(workspace=ws,
                      transcript=Transcript(out_dir / "transcript.jsonl"),
                      env={"VLLM_AGENT_OUT_DIR": str(out_dir)})
    cfg = LoopConfig(vllm_base_url="https://vllm.example",
                     vllm_model="m", max_iterations=1,
                     max_tokens=64, temperature=0,
                     tools_subset=["web_search"])
    await run_loop([{"role": "user", "content": "go"}], ctx, cfg)

    advertised = {t["function"]["name"] for t in captured["body"]["tools"]}
    assert advertised == {"web_search"}
```

- [ ] **Step 2: Verify failure**

```bash
cd vllm-agent && uv run pytest tests/test_loop.py::test_loop_tools_subset_restricts_palette -v
```
Expected: FAIL — `LoopConfig` has no `tools_subset` arg.

- [ ] **Step 3a: Add `tools_subset` to `LoopConfig`**

In `vllm-agent/src/vllm_agent/loop.py`, change `LoopConfig` to:

```python
@dataclass
class LoopConfig:
    vllm_base_url: str
    vllm_model: str
    max_iterations: int = 30
    max_tokens: int = 4096
    temperature: float = 0.2
    api_key: str | None = None
    request_timeout_s: float = 600.0
    tools_subset: list[str] | None = None   # if set, only these tool names are advertised
```

Change `_tools_schema` to accept the subset:

```python
def _tools_schema(subset: list[str] | None = None) -> list[dict[str, Any]]:
    if subset is None:
        return [t.schema for t in WORKER_TOOLS.values()]
    return [t.schema for name, t in WORKER_TOOLS.items() if name in subset]
```

In `run_loop`, change the two `_tools_schema()` calls (in the request body — there are two identical calls inside the `while True:` retry block) to:
```python
                        "tools": _tools_schema(cfg.tools_subset),
```

- [ ] **Step 3b: Add `session_dir` public method**

In `vllm-agent/src/vllm_agent/sessions.py`, add right after `_dir` (or merge them):

```python
    def session_dir(self, session_id: str) -> Path:
        """Public alias for the session's on-disk directory."""
        return self._dir(session_id)
```

In `vllm-agent/src/vllm_agent/api.py`, replace every `store._dir(...)` call with `store.session_dir(...)`. There are 4 sites (in `agent_session_start`, `agent_session_step`'s dead-status guard, `agent_session_step`'s sess_dir construction, and `agent_session_status`).

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/ -v
```
Expected: 70 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/loop.py vllm-agent/src/vllm_agent/sessions.py vllm-agent/src/vllm_agent/api.py vllm-agent/tests/test_loop.py
git commit -m "Plan B: LoopConfig.tools_subset + SessionStore.session_dir"
```

---

## Phase 3 — MCP shim refactor

The existing `mcp-server/vllm_mcp.py` has its own `_generate()` tool-call loop and `_ddg_search()` function. Both are duplicates of code now in `vllm_agent`. Refactor each MCP tool to delegate to `vllm_agent.loop.run_loop` with `tools_subset=["web_search"]`. Behavior must remain unchanged for callers.

---

## Task 6: Add `vllm-agent` as a dep of `mcp-server`

**Files:**
- Modify: `mcp-server/pyproject.toml`

- [ ] **Step 1: Update `pyproject.toml`**

Replace the existing `dependencies` block with:

```toml
dependencies = [
    "mcp>=1.2.0",
    "httpx>=0.27.0",
    "beautifulsoup4>=4.12",
    "pyyaml>=6.0",
    "vllm-agent",
]

[tool.uv.sources]
vllm-agent = { path = "../vllm-agent", editable = true }
```

(Keep existing scripts and build-system blocks unchanged.)

- [ ] **Step 2: Reinstall the venv**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && uv pip install -e .
```
Expected: installs successfully; `python -c "import vllm_agent; print(vllm_agent.__version__)"` works.

- [ ] **Step 3: Smoke-check**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python -c "import vllm_agent; print(vllm_agent.__version__)"
```
Expected: `0.1.0`.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/pyproject.toml
git commit -m "Plan B: mcp-server depends on local vllm-agent package"
```

---

## Task 7: Refactor `ask` and `converse` to delegate to `vllm_agent.loop.run_loop`

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

The existing `ask()` and `converse()` tools build messages, call `_generate()` (a local tool-call loop with web_search), and write the answer to disk. Replace `_generate` with a call to `vllm_agent.loop.run_loop` configured with `tools_subset=["web_search"]`. The output writing logic stays the same.

- [ ] **Step 1: Add the helper `_run_via_vllm_agent`** at the top of `mcp_server/vllm_mcp.py`, after the existing imports:

```python
# ---------------------------------------------------------------------------
# Helper: run a single-turn or multi-turn loop via vllm_agent.loop.run_loop
# with only web_search available. This is the new shared engine for
# ask/converse/critique/scaffold (replaces the local _generate).
# ---------------------------------------------------------------------------
import asyncio as _asyncio
from pathlib import Path as _Path

from vllm_agent.loop import LoopConfig as _LoopConfig, run_loop as _run_loop
from vllm_agent.tools import ToolContext as _ToolContext
from vllm_agent.tools import search as _search  # noqa: F401  registers web_search
from vllm_agent.transcript import Transcript as _Transcript
from vllm_agent.workspace import Workspace as _Workspace


async def _run_via_vllm_agent(
    msgs: list[dict[str, Any]],
    *,
    out_dir: Path,
    model: str | None,
    max_iterations: int,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    """Run a chat-completion + web_search loop via the shared agent runtime.

    Returns: {"answer", "iterations", "search_log"}.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace = _Workspace.resolve(None)  # CWD; tools won't actually use it
    ctx = _ToolContext(
        workspace=workspace,
        transcript=_Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )
    cfg = _LoopConfig(
        vllm_base_url=VLLM_BASE_URL,
        vllm_model=model or VLLM_MODEL,
        max_iterations=max_iterations,
        max_tokens=max_tokens,
        temperature=temperature,
        api_key=VLLM_API_KEY or None,
        tools_subset=["web_search"],
    )
    result = await _run_loop(msgs, ctx, cfg)
    # Extract search_log from transcript (web_search records).
    search_log: list[dict[str, Any]] = []
    tpath = out_dir / "transcript.jsonl"
    if tpath.exists():
        for line in tpath.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "tool_call" and rec.get("tool") == "web_search":
                args = rec.get("args") or {}
                r = rec.get("result") or {}
                if "error" in r:
                    search_log.append({"query": args.get("query", ""), "error": r["error"]})
                else:
                    search_log.append({"query": args.get("query", ""),
                                       "n_results": len(r.get("results", []))})
    return {
        "answer": result.final_message_content or "",
        "iterations": result.iterations,
        "search_log": search_log,
    }
```

- [ ] **Step 2: Refactor `ask`**

Replace the entire `ask()` function body (the part that builds messages, calls `_generate`, writes output) with:

```python
@mcp.tool()
async def ask(
    prompt: str,
    out_path: str,
    system: str | None = None,
    model: str | None = None,
    max_iterations: int = 3,
    max_results: int = 5,    # kept for back-compat; ignored now (web_search uses its own default)
    max_tokens: int = 1500,
    temperature: float = 0.3,
    include_log: bool = False,
) -> dict[str, Any]:
    """Single-turn ask. The model has `web_search` available and the answer is
    written to `out_path` — only metadata returns to the caller.

    Returns: {"path", "bytes_written", "iterations", "search_log",
              "duration_s", "answer_preview"}.
    """
    sys_prompt = DEFAULT_SYSTEM if system is None else system
    msgs: list[dict[str, Any]] = []
    if sys_prompt:
        msgs.append({"role": "system", "content": sys_prompt})
    msgs.append({"role": "user", "content": prompt})

    p = _Path(out_path).expanduser()
    out_dir_ag = p.parent if p.parent != _Path("") else _Path.cwd()
    t0 = time.perf_counter()
    result = await _run_via_vllm_agent(
        msgs, out_dir=out_dir_ag, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature,
    )
    elapsed = time.perf_counter() - t0
    p_written = _write_answer(out_path, result["answer"], result["search_log"],
                              result["iterations"], include_log)
    return {
        "path": str(p_written),
        "bytes_written": p_written.stat().st_size,
        "iterations": result["iterations"],
        "search_log": result["search_log"],
        "duration_s": round(elapsed, 2),
        "answer_preview": result["answer"][:80],
    }
```

- [ ] **Step 3: Refactor `converse`**

Replace `converse()`'s body with the same pattern (it just doesn't prepend a system prompt — uses the caller's messages as-is):

```python
@mcp.tool()
async def converse(
    messages: list[dict[str, Any]],
    out_path: str,
    model: str | None = None,
    max_iterations: int = 3,
    max_results: int = 5,
    max_tokens: int = 1500,
    temperature: float = 0.3,
    include_log: bool = False,
) -> dict[str, Any]:
    """Multi-turn dialog. Pass an OpenAI-format messages list; the model has
    `web_search` available and the final assistant reply is written to `out_path`.

    Returns the same metadata shape as `ask`.
    """
    p = _Path(out_path).expanduser()
    out_dir_ag = p.parent if p.parent != _Path("") else _Path.cwd()
    t0 = time.perf_counter()
    result = await _run_via_vllm_agent(
        list(messages), out_dir=out_dir_ag, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature,
    )
    elapsed = time.perf_counter() - t0
    p_written = _write_answer(out_path, result["answer"], result["search_log"],
                              result["iterations"], include_log)
    return {
        "path": str(p_written),
        "bytes_written": p_written.stat().st_size,
        "iterations": result["iterations"],
        "search_log": result["search_log"],
        "duration_s": round(elapsed, 2),
        "answer_preview": result["answer"][:80],
    }
```

- [ ] **Step 4: Manual smoke test**

Since there's no test file for mcp-server, do a manual smoke test:

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && \
  python -c "
import asyncio
from vllm_mcp import ask
async def main():
    out = await ask('say hi', '/tmp/ask_test.txt', max_tokens=64)
    print(out)
asyncio.run(main())
"
```
Expected: writes to `/tmp/ask_test.txt`, returns dict with path/bytes_written/iterations/etc. If the live VM endpoint is set in `.mcp.json` and reachable via the env vars, it should produce real output. If not, you'll see an httpx error — that's OK as long as the function call itself succeeds (the error surfacing is the expected behavior).

If the manual test fails with an import error or syntax error, fix and retry. If it fails with a network error to the VM, that's environmental and acceptable for now.

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan B: ask + converse delegate to vllm_agent.loop.run_loop"
```

---

## Task 8: Refactor `critique` and `scaffold` to delegate

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

- [ ] **Step 1: Refactor `critique`** — replace its body with:

```python
@mcp.tool()
async def critique(
    prompt: str,
    draft: str,
    out_path: str,
    model: str | None = None,
    max_iterations: int = 2,
    max_results: int = 4,
    max_tokens: int = 2000,
    temperature: float = 0.2,
    include_log: bool = False,
) -> dict[str, Any]:
    """Take an original task + a draft answer; produce a corrected version.

    Returns the same metadata shape as `ask`.
    """
    system = (
        "You are a strict senior code reviewer. Given an original task and a draft "
        "answer, identify bugs, missing edge cases, type errors, and style issues, "
        "then output the CORRECTED VERSION ONLY — not the critique. Use the "
        "`web_search` tool to verify any API names, version-specific behavior, or "
        "deprecations you are unsure about. Keep the same shape (same files, same "
        "names) unless correctness requires otherwise. Do not add prose."
    )
    user = (
        f"=== ORIGINAL TASK ===\n{prompt}\n\n"
        f"=== DRAFT TO REVIEW ===\n{draft}\n\n"
        f"=== YOUR TASK ===\nProduce the corrected version. Output only the "
        f"corrected artifact (code, files, etc.), no commentary."
    )
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    p = _Path(out_path).expanduser()
    out_dir_ag = p.parent if p.parent != _Path("") else _Path.cwd()
    t0 = time.perf_counter()
    result = await _run_via_vllm_agent(
        msgs, out_dir=out_dir_ag, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature,
    )
    elapsed = time.perf_counter() - t0
    p_written = _write_answer(out_path, result["answer"], result["search_log"],
                              result["iterations"], include_log)
    return {
        "path": str(p_written),
        "bytes_written": p_written.stat().st_size,
        "iterations": result["iterations"],
        "search_log": result["search_log"],
        "duration_s": round(elapsed, 2),
        "answer_preview": result["answer"][:80],
    }
```

- [ ] **Step 2: Refactor `scaffold`** — its retry-for-missing-files logic stays, but the inner `_generate` calls become `_run_via_vllm_agent`:

Replace `scaffold()`'s body. The new version is structurally identical to the old one but every `_generate(...)` call becomes `_run_via_vllm_agent(...)` with the same args. Keep the FILE-block parsing, the retry loop for `require_files`, and the warnings logic exactly as is.

The cleanest replacement is to find the two `result = await _generate(...)` calls inside `scaffold` and replace each with `result_dict = await _run_via_vllm_agent(...)` returning `{"answer", "iterations", "search_log"}`. Then update the variable references: `result["answer"]` → `result_dict["answer"]`, etc.

```python
@mcp.tool()
async def scaffold(
    prompt: str,
    out_dir: str,
    system: str | None = None,
    model: str | None = None,
    max_iterations: int = 3,
    max_results: int = 5,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    minimize_search: bool = True,
    require_files: list[str] | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Multi-file project generation. (See module docstring for details.)"""
    if system is not None:
        sys_prompt = system
    elif minimize_search:
        sys_prompt = (
            "You generate complete projects. Output ONLY file content blocks in this "
            "exact format, with no prose before, between, or after:\n\n"
            "=== FILE: relative/path ===\n```language\n<file content>\n```\n\n"
            "CRITICAL: web_search is available but you should NOT use it unless "
            "absolutely necessary — you already know the common APIs, frameworks, "
            "and license texts. Output FILE blocks immediately. Each search costs "
            "output tokens that would otherwise produce code."
        )
    else:
        sys_prompt = (
            "You generate complete projects. Output ONLY file content blocks in this "
            "exact format, with no prose before, between, or after:\n\n"
            "=== FILE: relative/path ===\n```language\n<file content>\n```\n\n"
            "You may use the `web_search` tool first if you need to confirm current "
            "API names, versions, or recent docs. After any searches, output ONLY the "
            "FILE blocks — no preamble, no commentary."
        )

    base = _Path(out_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt},
    ]
    t0 = time.perf_counter()
    result_dict = await _run_via_vllm_agent(
        msgs, out_dir=base, model=model,
        max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature,
    )
    written, warnings = _parse_and_write(result_dict["answer"], base)
    iterations_total = result_dict["iterations"]
    search_log = list(result_dict["search_log"])
    retries_used = 0

    if require_files:
        required_set = {p.lstrip("./").rstrip("/") for p in require_files}
        for _ in range(max_retries):
            written_rels = {Path(w["path"]).relative_to(base).as_posix() for w in written}
            missing = sorted(required_set - written_rels)
            if not missing:
                break
            retries_used += 1
            retry_prompt = (
                "You generated a partial project. Output ONLY the FILE blocks for "
                "these MISSING paths, in the same `=== FILE: <path> ===` format. "
                "Do NOT regenerate files that already exist.\n\n"
                "Missing files:\n  - " + "\n  - ".join(missing) + "\n\n"
                "Original task (for context):\n"
                + (prompt[:1500] + (" ... [truncated]" if len(prompt) > 1500 else ""))
            )
            retry_result = await _run_via_vllm_agent(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": retry_prompt},
                ],
                out_dir=base, model=model,
                max_iterations=max_iterations,
                max_tokens=max_tokens, temperature=temperature,
            )
            retry_written, retry_warnings = _parse_and_write(retry_result["answer"], base)
            written.extend(retry_written)
            warnings.extend(retry_warnings)
            iterations_total += retry_result["iterations"]
            search_log.extend(retry_result["search_log"])
            if not retry_written:
                warnings.append(f"Retry {retries_used} produced no FILE blocks; aborting.")
                break

    elapsed = time.perf_counter() - t0

    if not written:
        warnings.append("No FILE blocks parsed from model output; nothing written.")

    final_missing: list[str] = []
    if require_files:
        written_rels = {Path(w["path"]).relative_to(base).as_posix() for w in written}
        final_missing = sorted({p.lstrip("./").rstrip("/") for p in require_files} - written_rels)

    return {
        "out_dir": str(base),
        "files": written,
        "n_files": len(written),
        "iterations": iterations_total,
        "retries": retries_used,
        "search_log": search_log,
        "duration_s": round(elapsed, 2),
        "warnings": warnings,
        "missing_required": final_missing,
    }
```

- [ ] **Step 3: Manual smoke test**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && \
  python -c "
import asyncio
from vllm_mcp import scaffold
async def main():
    out = await scaffold('Generate a tiny pyproject.toml with name=demo and a single hello.py that prints hi.', '/tmp/scaffold_test', max_tokens=512)
    print(out)
asyncio.run(main())
"
```
Expected: produces 1-2 files in `/tmp/scaffold_test/`. If the VM is reachable.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan B: critique + scaffold delegate to vllm_agent.loop.run_loop"
```

---

## Task 9: Remove dead `_generate` and `_ddg_search` code from `vllm_mcp.py`

After Tasks 7-8, `_generate` and `_ddg_search` (and helpers `_browser_headers`, `_ddg_throttle`, `_BROWSER_UAS`, `_ddg_last_call`, `_ddg_lock`) are unused.

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

- [ ] **Step 1: Verify `_generate` and `_ddg_search` are no longer referenced**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && grep -n '_generate\|_ddg_search\|_ddg_throttle\|_browser_headers' vllm_mcp.py
```
Expected: zero matches if Tasks 7-8 were thorough; otherwise grep flags remaining call sites that need to be cleaned up first.

- [ ] **Step 2: Delete the dead code**

In `vllm_mcp.py`, remove these blocks (use the `=============` comment headers as anchors):
- The "Browser-fingerprint headers (rotated UA + minimal real headers)" block (defines `_BROWSER_UAS`, `_browser_headers`)
- The "DuckDuckGo search (rate-limited, browser-headered)" block (defines `_ddg_last_call`, `_ddg_lock`, `_ddg_throttle`, `_ddg_search`)
- The "Core: chat-completion loop with web search tool always available" block (defines `_WEB_SEARCH_SCHEMA`, `_vllm_headers`, `_generate`)
- The `import random`, `import time` (only if no longer needed elsewhere — `time.perf_counter` is still used in ask/converse/critique/scaffold via the new code, so KEEP `import time`)
- Unused imports: `import asyncio` if no longer needed at top level (the new helper imports `asyncio as _asyncio`); `from bs4 import BeautifulSoup` since BS4 is used only by the deleted `_ddg_search`; `from urllib.parse import parse_qs, urlparse, unquote` if any.

Run the dead-code grep again to confirm nothing survives:
```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && grep -n '_BROWSER_UAS\|_browser_headers\|_ddg_\|_WEB_SEARCH_SCHEMA\|_vllm_headers\|_generate\|BeautifulSoup' vllm_mcp.py
```
Expected: 0 matches except for `_vllm_headers` if it's still used by `health()` (check). If `health()` uses it, KEEP `_vllm_headers`.

- [ ] **Step 3: Verify the module still imports**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python -c "import vllm_mcp; print('imports OK')"
```
Expected: `imports OK`.

- [ ] **Step 4: Manual smoke test of the existing tools to confirm no regression**

```bash
python -c "
import asyncio
from vllm_mcp import ask
async def main():
    out = await ask('say hi', '/tmp/ask_test2.txt', max_tokens=64)
    print(out)
asyncio.run(main())
"
```
Expected: works the same as Task 7.

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan B: remove dead _generate / _ddg_search code"
```

---

## Phase 4 — New MCP tools

---

## Task 10: `list_skills` MCP tool

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

- [ ] **Step 1: Add the tool** at the end of `vllm_mcp.py` (just before `def main()`):

```python
# =============================================================================
# Tools: skill discovery
# =============================================================================

from vllm_agent.skills import SkillLoader as _SkillLoader


@mcp.tool()
async def list_skills() -> list[dict[str, Any]]:
    """List all skills discoverable from configured roots (project, user,
    superpowers). Returns: [{"name", "source", "path", "description"}, ...].
    Skill names are passed to `agent_run(skill=...)` and `agent_session_start(skill=...)`.
    """
    return _SkillLoader().list_skills()
```

- [ ] **Step 2: Verify it imports**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python -c "
import asyncio
from vllm_mcp import list_skills
print(asyncio.run(list_skills())[:3])
"
```
Expected: a list of skill dicts (or empty if no skill roots are populated). Should not raise.

- [ ] **Step 3: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan B: add list_skills MCP tool"
```

---

## Task 11: `agent_run` MCP tool with local/remote dispatch

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

The MCP tool exposes `agent_run` to Claude. Internally it dispatches based on `mode`:
- `mode=local`: import `vllm_agent.api.agent_run` and call it in-process.
- `mode=remote`: POST to `VLLM_AGENT_URL` (env var, set by the launch script after VM provisioning).

- [ ] **Step 1: Add the imports + helper** at the top of `vllm_mcp.py` (alongside the other vllm_agent imports):

```python
from vllm_agent.api import (
    AgentRunRequest as _AgentRunRequest,
    agent_run as _agent_run_local,
)

VLLM_AGENT_URL = os.environ.get("VLLM_AGENT_URL", "")  # e.g. http://10.x.y.z:8088


async def _agent_run_remote(req: _AgentRunRequest) -> dict[str, Any]:
    """POST the request to the VM's vllm-agent serve endpoint."""
    if not VLLM_AGENT_URL:
        return {"status": "error",
                "error": "VLLM_AGENT_URL not set; cannot use mode=remote"}
    body = {
        "task": req.task, "skill": req.skill, "mode": "remote",
        "workdir": req.workdir, "out_dir": req.out_dir, "model": req.model,
        "max_iterations": req.max_iterations, "max_tokens": req.max_tokens,
        "temperature": req.temperature, "timeout_s": req.timeout_s,
        "extra_context": req.extra_context,
    }
    async with httpx.AsyncClient(timeout=float(req.timeout_s + 30)) as client:
        r = await client.post(f"{VLLM_AGENT_URL}/run", json=body)
    if r.status_code != 200:
        return {"status": "error", "error": f"VM agent HTTP {r.status_code}: {r.text[:300]}"}
    return r.json()
```

- [ ] **Step 2: Add the MCP tool**

```python
@mcp.tool()
async def agent_run(
    task: str,
    skill: str | None = None,
    mode: str = "remote",
    workdir: str | None = None,
    out_dir: str | None = None,
    model: str | None = None,
    max_iterations: int = 30,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    timeout_s: int = 1800,
    extra_context: list[str] | None = None,
) -> dict[str, Any]:
    """Dispatch a coding-agent task to the vllm-rtx5090 backend.

    `mode='local'` runs the agent loop in this process (worker tools execute on
    the user's machine; bash requires VLLM_AGENT_LOCAL_BASH=1).
    `mode='remote'` POSTs to the VM-side vllm-agent serve (worker tools execute
    in the VM; full Bash; requires VLLM_AGENT_URL to be set).

    Returns metadata only: run_id, out_dir, summary_path, files_changed,
    diff_path, iterations, duration_s, status, error, search_log.
    The actual agent output is on disk under out_dir.
    """
    req = _AgentRunRequest(
        task=task, skill=skill, mode=mode, workdir=workdir, out_dir=out_dir,
        model=model, max_iterations=max_iterations, max_tokens=max_tokens,
        temperature=temperature, timeout_s=timeout_s, extra_context=extra_context,
    )
    if mode == "local":
        from dataclasses import asdict
        result = await _agent_run_local(req)
        return asdict(result)
    else:
        return await _agent_run_remote(req)
```

- [ ] **Step 3: Manual smoke test (local mode)**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && \
  VLLM_AGENT_LOCAL_BASH=1 python -c "
import asyncio
from vllm_mcp import agent_run
async def main():
    out = await agent_run(
        task='Call the finish() tool with summary \"smoke ok\".',
        mode='local',
        workdir='/tmp',
        out_dir='/tmp/agent_run_test',
        max_iterations=3,
        max_tokens=256,
    )
    print(out)
asyncio.run(main())
"
```
Expected: returns dict with `status: "ok"`, summary file at `/tmp/agent_run_test/summary.md`. If VM unreachable, the local-mode call still uses VLLM_BASE_URL from env, which points to the VM, so a network failure means the VM endpoint is down (acceptable in CI; flag if persistent).

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan B: add agent_run MCP tool with local/remote dispatch"
```

---

## Task 12: `agent_session_*` MCP tools (start/step/status/stop)

Four MCP tools, all dispatched through the same local/remote split as `agent_run`. Single commit.

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

- [ ] **Step 1: Add the imports + remote-dispatch helpers**

At the top alongside other vllm_agent imports:

```python
from vllm_agent.api import (
    AgentSessionStartRequest as _AgentSessionStartRequest,
    agent_session_start as _ass_local,
    agent_session_step as _aststep_local,
    agent_session_status as _aststatus_local,
    agent_session_stop as _aststop_local,
)
```

Add helpers:

```python
async def _http_session_start(body: dict) -> dict[str, Any]:
    if not VLLM_AGENT_URL:
        return {"status": "error", "error": "VLLM_AGENT_URL not set"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{VLLM_AGENT_URL}/session", json=body)
    return r.json() if r.status_code == 200 else {"status": "error",
                                                   "error": f"HTTP {r.status_code}: {r.text[:300]}"}


async def _http_session_step(session_id: str, body: dict) -> dict[str, Any]:
    if not VLLM_AGENT_URL:
        return {"status": "error", "error": "VLLM_AGENT_URL not set"}
    async with httpx.AsyncClient(timeout=1800.0) as client:
        r = await client.post(f"{VLLM_AGENT_URL}/session/{session_id}/step", json=body)
    return r.json() if r.status_code == 200 else {"status": "error",
                                                   "error": f"HTTP {r.status_code}: {r.text[:300]}"}


async def _http_session_status(session_id: str) -> dict[str, Any]:
    if not VLLM_AGENT_URL:
        return {"status": "error", "error": "VLLM_AGENT_URL not set"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{VLLM_AGENT_URL}/session/{session_id}")
    return r.json() if r.status_code == 200 else {"status": "error",
                                                   "error": f"HTTP {r.status_code}: {r.text[:300]}"}


async def _http_session_stop(session_id: str) -> dict[str, Any]:
    if not VLLM_AGENT_URL:
        return {"status": "error", "error": "VLLM_AGENT_URL not set"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{VLLM_AGENT_URL}/session/{session_id}/stop")
    return r.json() if r.status_code == 200 else {"status": "error",
                                                   "error": f"HTTP {r.status_code}: {r.text[:300]}"}
```

- [ ] **Step 2: Add the 4 MCP tools**

```python
@mcp.tool()
async def agent_session_start(
    goal: str,
    skill: str | None = None,
    mode: str = "remote",
    workdir: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Start a long-running agent session. Returns: {session_id, out_dir, status}."""
    if mode == "local":
        from dataclasses import asdict
        req = _AgentSessionStartRequest(goal=goal, skill=skill, mode=mode,
                                        workdir=workdir, model=model)
        return asdict(await _ass_local(req))
    return await _http_session_start({
        "goal": goal, "skill": skill, "mode": "remote",
        "workdir": workdir, "model": model,
    })


@mcp.tool()
async def agent_session_step(
    session_id: str,
    nudge: str | None = None,
    max_iterations: int = 10,
    mode: str = "remote",
) -> dict[str, Any]:
    """Run one step of a session. Returns step metadata including step_status."""
    if mode == "local":
        from dataclasses import asdict
        return asdict(await _aststep_local(session_id, nudge=nudge,
                                            max_iterations=max_iterations))
    return await _http_session_step(session_id, {
        "nudge": nudge, "max_iterations": max_iterations,
    })


@mcp.tool()
async def agent_session_status(session_id: str, mode: str = "remote") -> dict[str, Any]:
    """Get the current state of a session."""
    if mode == "local":
        from dataclasses import asdict
        return asdict(await _aststatus_local(session_id))
    return await _http_session_status(session_id)


@mcp.tool()
async def agent_session_stop(session_id: str, mode: str = "remote") -> dict[str, Any]:
    """Stop a session. Subsequent steps return immediately with status=stopped."""
    if mode == "local":
        from dataclasses import asdict
        return asdict(await _aststop_local(session_id))
    return await _http_session_stop(session_id)
```

- [ ] **Step 3: Smoke test (local mode)**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python -c "
import asyncio
from vllm_mcp import agent_session_start, agent_session_step, agent_session_status, agent_session_stop
async def main():
    s = await agent_session_start(goal='do nothing', mode='local', workdir='/tmp')
    print('start:', s)
    step = await agent_session_step(s['session_id'], mode='local', max_iterations=2)
    print('step:', step)
    status = await agent_session_status(s['session_id'], mode='local')
    print('status:', status)
    stop = await agent_session_stop(s['session_id'], mode='local')
    print('stop:', stop)
asyncio.run(main())
"
```
Expected: a session is created, one step runs against the VM's vLLM, status returns metadata, stop sets status to "stopped". If VM unreachable, expect HTTP errors but the local-mode dispatch shouldn't crash.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan B: add agent_session_start/step/status/stop MCP tools"
```

---

## Phase 5 — VM provisioning

These tasks edit the cloud-init template + launch script. **Live testing requires re-provisioning the VM**, which is out of scope for the implementer; document the manual verification steps.

---

## Task 13: Update `profiles/rtx-inference.yaml.tpl` to install `vllm-agent` in the VM

**Files:**
- Modify: `profiles/rtx-inference.yaml.tpl`

The cloud-init template currently installs vLLM. Add steps to:
1. Clone the rtx_5090_dev repo into the VM
2. Install `vllm-agent` from the cloned tree (`uv pip install -e ./vllm-agent`)
3. Create a systemd unit `vllm-agent.service` that runs `vllm-agent serve --host 0.0.0.0 --port 8088`
4. Open port 8088 (already open via the LXD bridge — no firewall change needed inside the VM)

- [ ] **Step 1: Read the existing template**

```bash
cat /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/profiles/rtx-inference.yaml.tpl
```
Identify where the `runcmd` block is for vLLM install. The new install needs to happen there.

- [ ] **Step 2: Edit the template**

Add to the `runcmd` block (or equivalent), after vLLM is installed and started:

```yaml
  # Install vllm-agent from the rtx_5090_dev repo (pulled by user via lxc file push or git clone)
  - su - ubuntu -c 'cd ~ && (test -d rtx_5090_dev || git clone https://github.com/vantagecompute/rtx-inference.git rtx_5090_dev) && cd rtx_5090_dev/vllm-agent && uv venv .venv-agent && .venv-agent/bin/pip install -e .'

  # Install systemd unit for vllm-agent serve
  - |
    cat > /etc/systemd/system/vllm-agent.service <<'EOF'
    [Unit]
    Description=vllm-agent HTTP server (agent runtime backed by local vLLM)
    After=network-online.target vllm.service
    Wants=network-online.target

    [Service]
    Type=simple
    User=ubuntu
    Group=ubuntu
    WorkingDirectory=/home/ubuntu/rtx_5090_dev/vllm-agent
    Environment=VLLM_BASE_URL=http://127.0.0.1:8000
    Environment=VLLM_MODEL=__VLLM_MODEL__
    ExecStart=/home/ubuntu/rtx_5090_dev/vllm-agent/.venv-agent/bin/vllm-agent serve --host 0.0.0.0 --port 8088
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    EOF
  - systemctl daemon-reload
  - systemctl enable --now vllm-agent.service
```

Where `__VLLM_MODEL__` matches the existing template token (the launch script will sed-replace it).

**Note:** The git URL above (`https://github.com/vantagecompute/rtx-inference.git`) is the existing remote per `git remote -v`. If the repo is private and the VM doesn't have credentials, the user will need to lxc-file-push the package instead. Document this as a caveat in the launch script docs (Task 14).

- [ ] **Step 3: Smoke-validate the YAML**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev && python -c "
import yaml
with open('profiles/rtx-inference.yaml.tpl') as f:
    text = f.read()
# Substitute placeholders for parsing
text = text.replace('__VLLM_MODEL__', 'placeholder').replace('__PROFILE_NAME__', 'p').replace('__VLLM_MAX_LEN__', '32768').replace('__VLLM_GPU_UTIL__', '0.92').replace('__VLLM_QUANT__', 'awq').replace('__VLLM_API_KEY_ARG__', '').replace('__BRIDGE__', 'br').replace('__STORAGE_POOL__', 'pool').replace('__ROOT_SIZE__', '100GiB').replace('__LIMITS_CPU__', '4').replace('__LIMITS_MEMORY__', '32GiB')
yaml.safe_load(text)
print('yaml OK')
"
```
Expected: `yaml OK`. If YAML errors, fix indentation or quoting.

- [ ] **Step 4: Commit**

```bash
git add profiles/rtx-inference.yaml.tpl
git commit -m "Plan B: cloud-init installs vllm-agent + systemd unit in VM"
```

---

## Task 14: Update `launch-inference.sh` to expose port 8088 and write VLLM_AGENT_URL into `.mcp.json`

**Files:**
- Modify: `launch-inference.sh`

The script already updates `.mcp.json` after VM provisioning with `VLLM_BASE_URL` etc. Extend it to also write `VLLM_AGENT_URL=http://${VM_IP}:8088`.

- [ ] **Step 1: Find the `.mcp.json` update block**

```bash
grep -n 'VLLM_BASE_URL.*VLLM_MODEL' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/launch-inference.sh
```
This finds the python heredoc that updates `.mcp.json`. Add `VLLM_AGENT_URL` to it.

- [ ] **Step 2: Edit the heredoc**

Inside `launch-inference.sh`, find the python block that updates `.mcp.json` (it's a `python3 - "$MCP_JSON" "http://${VM_IP}:${PORT}" "$MODEL" ...` heredoc). Modify it to ALSO accept and write `VLLM_AGENT_URL`:

a) Change the call:
```bash
python3 - "$MCP_JSON" "http://${VM_IP}:${PORT}" "$MODEL" "$DDG_INTERVAL" "$API_KEY" "http://${VM_IP}:8088" <<'PYEOF'
```

b) Inside the heredoc, change the args unpack:
```python
path, base_url, model, ddg_interval, api_key, agent_url = sys.argv[1:7]
```

c) After the existing `env["VLLM_MODEL"] = model` line, add:
```python
env["VLLM_AGENT_URL"] = agent_url
```

- [ ] **Step 3: Update the help / final-output block**

Find the `cat <<EOF` block at the end that prints the example MCP config. Add `VLLM_AGENT_URL` to the env dict shown.

In the printed final-output block:
```
  Endpoint:        $ENDPOINT
  Agent endpoint:  http://${VM_IP}:8088
```
Add the agent-endpoint line after the existing endpoint line.

- [ ] **Step 4: Add a `vllm-agent` health probe to the script**

After the existing `vLLM /v1/models` probe (the `for i in $(seq 1 90); do ...` block), add a similar block for `vllm-agent`:

```bash
# ---------- N. Poll vllm-agent /health until ready ----------
log "Polling vllm-agent /health (waits for systemd unit + uv install on first boot)..."
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
```
This is non-fatal — if vllm-agent fails to come up, the user still has a working vLLM endpoint and can investigate.

- [ ] **Step 5: Bash-syntax-check the script**

```bash
bash -n /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/launch-inference.sh && echo "syntax OK"
```
Expected: `syntax OK`.

- [ ] **Step 6: Commit**

```bash
git add launch-inference.sh
git commit -m "Plan B: launch script wires VLLM_AGENT_URL + probes /health"
```

---

## Phase 6 — Documentation

---

## Task 15: Update `README.md` with the new tool surface

**Files:**
- Modify: `README.md`

The current README is a one-line stub (`# rtx-inference`). Replace it with a real README that documents:
- What the project is
- How to provision the VM
- The MCP tool surface (new + existing)
- Mode selection guidance (local vs remote)
- The `VLLM_AGENT_LOCAL_BASH=1` opt-in

- [ ] **Step 1: Write the new README**

Replace `/home/bdx/allcode/github/vantagecompute/rtx_5090_dev/README.md` with:

```markdown
# rtx-inference

Self-hosted Qwen3-Coder-30B-A3B vLLM endpoint behind an MCP server, with a
companion agent runtime (`vllm-agent`) so Claude Code can offload bulk codegen
and iterative coding work to the local 5090.

## Components

- **vLLM** — runs in an LXD VM with GPU passthrough, serves an OpenAI-compatible
  chat-completions endpoint.
- **vllm-agent** — a Python runtime that wraps vLLM with worker tools
  (read_file, write_file, edit_file, bash, grep, glob, web_search, finish), a
  tool-calling loop, skill loading, and session storage. Runs both as a Python
  library and as a FastAPI HTTP server (`vllm-agent serve`) inside the VM.
- **mcp-server** — the MCP shim that exposes vllm-agent's capabilities to
  Claude Code.

## Provisioning

```bash
./launch-inference.sh --lxd-host USER@LXD-CLUSTER-MEMBER
```

This:
1. Creates an LXD VM with GPU passthrough.
2. Installs vLLM and serves it on port 8000.
3. Installs `vllm-agent serve` as a systemd unit on port 8088.
4. Updates `.mcp.json` with the VM's URLs (`VLLM_BASE_URL`, `VLLM_AGENT_URL`).

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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Plan B: README documents new MCP tools and mode selection"
```

---

## Task 16: Add `CLAUDE.md` with routing guidance for Claude Code

**Files:**
- Create: `CLAUDE.md`

Claude Code automatically loads `CLAUDE.md` from the project root. Use it to teach Claude when to dispatch to vllm-rtx5090 vs do work itself.

- [ ] **Step 1: Create the file**

`/home/bdx/allcode/github/vantagecompute/rtx_5090_dev/CLAUDE.md`:

```markdown
# rtx-inference — Claude Code project notes

This project hosts a local Qwen3-Coder-30B vLLM endpoint and a companion agent
runtime (`vllm-agent`) exposed to you via MCP. Use it to offload heavy lifting.

## When to dispatch to vllm-rtx5090

Prefer `agent_run` (or `agent_session_*`) over doing the work yourself when:
- Bulk code generation: whole-file or whole-module drafts, scaffolds, refactors.
- Long-context analysis: read a large directory and summarize / audit / review.
- Self-iterating coding work: write code, run tests, fix failures — leave the
  whole loop on the worker side instead of bouncing back per step.

Do the work yourself (Read/Edit/Bash/etc.) when:
- The task needs design judgment or cross-file reasoning that benefits from
  Opus-class capability.
- The work is short enough that one tool call is faster than dispatching.
- You're orchestrating multi-step work that will use the worker's outputs.

## Mode selection

- `mode="remote"` (default): worker runs in the VM. Use this for anything
  long-running, destructive, or that runs package installs.
- `mode="local"`: worker runs on the user's machine. Use for quick read-only
  generate tasks. Bash requires `VLLM_AGENT_LOCAL_BASH=1` and is off by default.

## Skills

When dispatching a task that maps to a known workflow, pass `skill=`:
- `agent_run(skill="superpowers:test-driven-development", ...)` for TDD work.
- `agent_run(skill="superpowers:systematic-debugging", ...)` for bugs.
- `agent_run(skill="superpowers:writing-plans", ...)` for plan drafts.

Use `list_skills` to discover what's available.

## Output discipline

`agent_run` never returns raw model output. It returns metadata and writes
everything to `out_dir`:
- `summary.md` — worker's finish() summary
- `transcript.jsonl` — full conversation
- `files_changed.txt` — paths touched
- `diff.patch` — git diff (remote mode)

If you want to inspect the worker's output, `Read` the files in `out_dir`.
Don't paste raw transcript into the conversation.

## Existing tools (also available)

`ask` / `converse` / `critique` / `scaffold` — these were the original tools.
They now delegate to vllm-agent internally with `tools_subset=["web_search"]`,
so they behave as before but share infrastructure with the agent runtime.

## Existing memory

If `~/.claude/projects/-home-bdx-allcode-github-vantagecompute-rtx-5090-dev/memory/`
has facts about the user or project, prefer those over assumptions.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Plan B: CLAUDE.md routes work between Claude and vllm-rtx5090"
```

---

## Final verification

- [ ] **Step 1: Full vllm-agent test suite**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/vllm-agent && uv run pytest -v
```
Expected: 69+ tests pass (61 baseline + ~8 new from Plan B Tasks 1-5).

- [ ] **Step 2: MCP-server smoke**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python -c "
import vllm_mcp
# All tool functions are decorated with @mcp.tool() — confirm they're registered.
tools = [n for n in dir(vllm_mcp) if not n.startswith('_')]
assert 'ask' in tools and 'agent_run' in tools and 'list_skills' in tools
print(f'mcp tools registered: {tools}')
"
```

- [ ] **Step 3: Bash syntax-check**

```bash
bash -n /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/launch-inference.sh && echo "OK"
```

- [ ] **Step 4 (optional, requires VM access): Re-provision & smoke**

```bash
./launch-inference.sh --lxd-host USER@HOST  # destroys + recreates VM
# Then in Claude Code: call list_skills, then agent_run(mode='remote', task='Call finish() with summary "live ok"')
```

---

## Self-Review

### Spec coverage
| Spec section | Implemented in tasks |
|---|---|
| Plan A follow-up #1 (timeout status) | Task 1 |
| Plan A follow-up #2 (search_log) | Task 2 |
| Plan A follow-up #3 (404 handling) | Task 3 |
| Plan A follow-up #4 (status vocab) | Task 3 |
| Plan A follow-up #5 (HTTP session tests) | Task 4 |
| §6 Rollout step 3 (VM systemd) | Tasks 13, 14 |
| §6 Rollout step 4 (refactor existing tools) | Tasks 7, 8, 9 |
| §6 Rollout step 5 (new MCP tools) | Tasks 10, 11, 12 |
| §6 Rollout step 6 (README + CLAUDE.md) | Tasks 15, 16 |
| §6 Rollout step 7 (routing snippet) | Task 16 (CLAUDE.md) |

**Out of scope (deferred to Plan C if ever):** git push/fetch sync for `mode=remote` (the design spec mentions this as the way to surface `diff_path`; Plan B leaves it as `None`). Concurrent step guard. Scheduled cleanup of old session dirs.

### Placeholder scan
Done — no `TBD`, `TODO`, or "implement later" tokens. Every step has full code or a complete command.

### Type consistency
- `AgentRunResult.search_log: list[dict]` is consistent across api.py, server.py, and Task 2's test.
- `AgentSessionStepResult.step_status` is added in Task 3 and referenced in Tasks 4, 12.
- `LoopConfig.tools_subset: list[str] | None` is consistent across Tasks 5, 7, 8.
- MCP tool signatures (`agent_run`, `agent_session_*`) match `vllm_agent.api` request dataclass shapes.
- Env var names (`VLLM_AGENT_URL`, `VLLM_AGENT_LOCAL_BASH`, `VLLM_AGENT_SESSION_ROOT`, `VLLM_BASE_URL`, `VLLM_MODEL`) consistent across mcp-server, vllm-agent, launch script, and CLAUDE.md.

No issues found.
