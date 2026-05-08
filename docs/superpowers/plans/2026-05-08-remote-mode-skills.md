# Plan G: Remote-Mode Skills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `agent_run(skill="superpowers:test-driven-development", mode="remote")` work end-to-end. Today the worker container has no access to the orchestrator's `~/.claude` skill cache and returns `SkillNotFound`. Fix by resolving the skill markdown content on the orchestrator side (in the MCP shim) and shipping it in the request body via a new `skill_content` field. Worker uses content directly when provided, falls back to `SkillLoader` otherwise (preserving `mode=local` behavior).

**Architecture:** A single new field — `skill_content: str | None` — on `AgentRunRequest`, `AgentSessionStartRequest`, the corresponding Pydantic body models, and the `Session` persistence dataclass. The orchestrator-side MCP shim's `agent_run` / `agent_session_start` tools resolve the skill name via the local `SkillLoader` and inject content into the request before dispatching. The worker's `agent_run` / `agent_session_step` prefer `skill_content` when set, else fall back to the existing `SkillLoader` lookup.

**Tech Stack:** Python dataclasses, FastAPI Pydantic models, `httpx` (no new deps). Test changes use `respx` (already in dev deps) and existing `SessionStore` fixture patterns.

**Source spec:** `docs/superpowers/specs/2026-05-08-remote-mode-skills-design.md`

---

## File Structure

```
rtx_5090_dev/
├── vllm-agent/
│   ├── src/vllm_agent/
│   │   ├── api.py                  # MODIFY: + skill_content field + resolution logic
│   │   ├── sessions.py             # MODIFY: + skill_content on Session + create() arg
│   │   └── server.py               # MODIFY: + skill_content on 2 Pydantic body models
│   └── tests/
│       ├── test_api.py             # MODIFY: + 1 new test, update existing kwarg call sites
│       ├── test_server.py          # MODIFY: + 1 new test
│       └── test_sessions.py        # MODIFY: + 1 new test, update existing kwarg call sites
├── mcp-server/
│   └── vllm_mcp.py                 # MODIFY: resolve skill in 2 MCP tools, pass through to 2 helpers
└── README.md                        # MODIFY: remove "Skills in remote mode" limitation note
```

3 implementation tasks + 1 live-dogfood task. No new files.

---

## Task 1: vllm-agent — add `skill_content` field end-to-end

**Files:**
- Modify: `vllm-agent/src/vllm_agent/api.py`
- Modify: `vllm-agent/src/vllm_agent/sessions.py`
- Modify: `vllm-agent/src/vllm_agent/server.py`
- Modify: `vllm-agent/tests/test_api.py`
- Modify: `vllm-agent/tests/test_server.py`
- Modify: `vllm-agent/tests/test_sessions.py`

### Step 1: Add new failing tests (TDD red)

Append to `vllm-agent/tests/test_api.py`:

```python
@respx.mock
async def test_agent_run_uses_skill_content_when_provided(tmp_path, monkeypatch):
    """If skill_content is set in the request, the worker prompt includes it
    and SkillLoader is NOT consulted (we use a fake skill name to verify)."""
    captured: dict = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_capture)
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    req = AgentRunRequest(
        task="anything",
        skill="ignored:does-not-exist",
        skill_content="--- PROVIDED SKILL CONTENT ---",
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        max_iterations=1,
        max_tokens=64,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    sys_msg = next(m for m in captured["body"]["messages"] if m["role"] == "system")
    assert "PROVIDED SKILL CONTENT" in sys_msg["content"]
```

Append to `vllm-agent/tests/test_server.py`:

```python
@respx.mock
def test_run_endpoint_accepts_skill_content(client, tmp_path, monkeypatch):
    """POST /run with skill_content threads it into the worker's system prompt."""
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    captured: dict = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_capture)

    r = client.post("/run", json={
        "task": "x",
        "skill": "fake:skill",
        "skill_content": "--- INJECTED FROM HTTP BODY ---",
        "mode": "remote",
        "workdir": str(tmp_path),
        "out_dir": str(tmp_path / "out"),
        "max_iterations": 1,
        "max_tokens": 64,
    })
    assert r.status_code == 200
    sys_msg = next(m for m in captured["body"]["messages"] if m["role"] == "system")
    assert "INJECTED FROM HTTP BODY" in sys_msg["content"]
```

(Note: `test_server.py` already imports `json` and `respx` and has a `client` fixture.)

Append to `vllm-agent/tests/test_sessions.py`:

```python
def test_session_persists_skill_content(tmp_path):
    """skill_content survives session.json round-trip."""
    store = SessionStore(root=tmp_path)
    s = store.create(
        goal="g",
        skill="superpowers:tdd",
        skill_content="--- TDD SKILL TEXT ---",
        mode="local",
        workdir="/tmp",
        model=None,
    )
    s2 = store.load(s.session_id)
    assert s2.skill == "superpowers:tdd"
    assert s2.skill_content == "--- TDD SKILL TEXT ---"


def test_session_skill_content_defaults_to_none(tmp_path):
    """Sessions created without skill_content default to None (back-compat)."""
    store = SessionStore(root=tmp_path)
    s = store.create(
        goal="g",
        skill=None,
        skill_content=None,
        mode="local",
        workdir="/tmp",
        model=None,
    )
    s2 = store.load(s.session_id)
    assert s2.skill_content is None
```

### Step 2: Run the new tests to verify they fail

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/vllm-agent && uv run pytest tests/test_api.py::test_agent_run_uses_skill_content_when_provided tests/test_server.py::test_run_endpoint_accepts_skill_content tests/test_sessions.py::test_session_persists_skill_content tests/test_sessions.py::test_session_skill_content_defaults_to_none -v
```

Expected: 4 FAILs (TypeError on missing `skill_content` kwarg, or AttributeError on missing field).

### Step 3: Add `skill_content` to `AgentRunRequest` and `AgentSessionStartRequest`

In `vllm-agent/src/vllm_agent/api.py`, find the `AgentRunRequest` dataclass and add `skill_content` AFTER the existing `skill` field:

```python
@dataclass
class AgentRunRequest:
    task: str
    skill: str | None = None
    skill_content: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    out_dir: str | None = None
    model: str | None = None
    max_iterations: int = 30
    max_tokens: int = 4096
    temperature: float = 0.2
    timeout_s: int = 1800
    extra_context: list[str] | None = None
```

Find `AgentSessionStartRequest` and add `skill_content`:

```python
@dataclass
class AgentSessionStartRequest:
    goal: str
    skill: str | None = None
    skill_content: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None
```

### Step 4: Update `agent_run` to use `skill_content` when provided

In `agent_run`, find the line that resolves the skill via `SkillLoader`. It currently looks like:

```python
skill_content = SkillLoader().load_skill(req.skill) if req.skill else None
```

Replace with:

```python
if req.skill_content:
    skill_content = req.skill_content
elif req.skill:
    skill_content = SkillLoader().load_skill(req.skill)
else:
    skill_content = None
```

(Variable name reuse: `skill_content` here refers to the LOCAL variable in `agent_run`, not the field on the request. Both are clearly `str | None`. The existing local variable name is reused; don't rename it.)

### Step 5: Update `Session` dataclass in `sessions.py`

In `vllm-agent/src/vllm_agent/sessions.py`, find the `Session` dataclass and add `skill_content`:

```python
@dataclass
class Session:
    session_id: str
    goal: str
    skill: str | None
    skill_content: str | None
    mode: str
    workdir: str
    model: str | None
    status: SessionStatus
    started_at: float
    last_activity_at: float
    iterations_total: int = 0
    files_changed_total: list[str] = field(default_factory=list)
```

Update `SessionStore.create`'s signature to accept `skill_content` (use a kwarg-only argument to prevent positional-arg drift):

```python
def create(
    self,
    *,
    goal: str,
    skill: str | None,
    skill_content: str | None,
    mode: str,
    workdir: str,
    model: str | None,
) -> Session:
    sid = uuid.uuid4().hex[:12]
    now = time.time()
    s = Session(
        session_id=sid,
        goal=goal,
        skill=skill,
        skill_content=skill_content,
        mode=mode,
        workdir=workdir,
        model=model,
        status=SessionStatus.RUNNING,
        started_at=now,
        last_activity_at=now,
    )
    d = self._dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    self._write_meta(s)
    (d / "messages.jsonl").touch()
    (d / "transcript.jsonl").touch()
    return s
```

The `*,` after `self` makes ALL the params kwarg-only — callers must use `store.create(goal=..., skill=..., skill_content=..., ...)`. This prevents future arg-order mistakes.

The `_write_meta` and `load` paths use `asdict()` and `Session(**data)` respectively — they pick up the new field automatically. No changes needed there.

### Step 6: Update existing `SessionStore.create` callers to use kwargs

There are 4 call sites that currently use positional or partial-kwarg form. Update them all to full kwargs with `skill_content=None` where the test wasn't already passing it.

In `vllm-agent/tests/test_sessions.py`, find ALL calls to `store.create(...)`. Update each:

Before:
```python
s = store.create(goal="do X", skill="superpowers:tdd",
                 mode="remote", workdir="/tmp/repo", model=None)
```

After:
```python
s = store.create(
    goal="do X",
    skill="superpowers:tdd",
    skill_content=None,
    mode="remote",
    workdir="/tmp/repo",
    model=None,
)
```

Same shape for the other test cases — the existing tests pass `skill_content=None` to mean "old behavior".

In `vllm-agent/src/vllm_agent/api.py`, `agent_session_start` calls `store.create(...)`. Update it:

Before:
```python
s = store.create(goal=req.goal, skill=req.skill, mode=req.mode,
                 workdir=str(ws.root), model=req.model)
```

After:
```python
s = store.create(
    goal=req.goal,
    skill=req.skill,
    skill_content=req.skill_content,
    mode=req.mode,
    workdir=str(ws.root),
    model=req.model,
)
```

### Step 7: Update `agent_session_step` to use `Session.skill_content`

In `agent_session_step`, find the skill resolution line:

```python
skill_content = SkillLoader().load_skill(s.skill) if s.skill else None
```

Replace with:

```python
if s.skill_content:
    skill_content = s.skill_content
elif s.skill:
    skill_content = SkillLoader().load_skill(s.skill)
else:
    skill_content = None
```

### Step 8: Update server.py Pydantic body models

In `vllm-agent/src/vllm_agent/server.py`, find `RunBody` and `SessionStartBody`. Add `skill_content`:

```python
class RunBody(BaseModel):
    task: str
    skill: str | None = None
    skill_content: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    out_dir: str | None = None
    model: str | None = None
    max_iterations: int = 30
    max_tokens: int = 4096
    temperature: float = 0.2
    timeout_s: int = 1800
    extra_context: list[str] | None = None


class SessionStartBody(BaseModel):
    goal: str
    skill: str | None = None
    skill_content: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None
```

The endpoints already do `AgentRunRequest(**body.model_dump())` etc., so the new field flows through automatically.

### Step 9: Run the new tests + full suite

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/vllm-agent && uv run pytest -v
```

Expected: all PASS, including the 4 new tests added in Step 1. Old tests that previously called `SessionStore.create` positionally now use kwargs (Step 6); they should pass identically since the field defaults to `None`.

If any test fails with `TypeError: missing 1 required keyword-only argument`, that's a missed call site — find it via grep:

```bash
grep -rn 'store\.create(' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/vllm-agent/
```

and update.

Total tests should be 79 baseline + 4 new = **83 passing** (1 deselected).

### Step 10: Commit

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add vllm-agent/src/vllm_agent/api.py vllm-agent/src/vllm_agent/sessions.py vllm-agent/src/vllm_agent/server.py vllm-agent/tests/test_api.py vllm-agent/tests/test_server.py vllm-agent/tests/test_sessions.py
git commit -m "Plan G: vllm-agent supports skill_content for orchestrator-side skill resolution"
```

---

## Task 2: mcp-server — resolve skill locally before dispatch

**Files:**
- Modify: `mcp-server/vllm_mcp.py`

The MCP shim's `agent_run` and `agent_session_start` tools currently pass the user's `skill` name through to the request. Plan G changes this: when a `skill` name is set, resolve it locally via the existing `_SkillLoader` and pass `skill_content` through.

### Step 1: Update `agent_run` MCP tool

In `mcp-server/vllm_mcp.py`, find the `@mcp.tool() async def agent_run(...)` function. Update it:

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

    If `skill` is set, the orchestrator resolves the skill markdown via the
    local SkillLoader and ships the content in the request body, so remote
    workers don't need a skill cache.

    Returns metadata only: run_id, out_dir, summary_path, files_changed,
    diff_path, iterations, duration_s, status, error, search_log.
    The actual agent output is on disk under out_dir.
    """
    # Resolve skill content on the orchestrator side so the worker doesn't
    # need access to ~/.claude.
    skill_content: str | None = None
    if skill:
        try:
            skill_content = _SkillLoader().load_skill(skill)
        except _SkillNotFound as e:
            return {"status": "error", "error": str(e)}

    req = _AgentRunRequest(
        task=task,
        skill=skill,
        skill_content=skill_content,
        mode=mode,
        workdir=workdir,
        out_dir=out_dir,
        model=model,
        max_iterations=max_iterations,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
        extra_context=extra_context,
    )
    if mode == "local":
        result = await _agent_run_local(req)
        return asdict(result)
    return await _agent_run_remote(req)
```

The `_SkillLoader` and `_SkillNotFound` symbols are already imported at the top of the file (used by the `list_skills` tool — verify with `grep '_SkillLoader\|_SkillNotFound' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server/vllm_mcp.py`). If they're imported with different names (e.g. just `SkillLoader, SkillNotFound`), use those.

### Step 2: Update `_agent_run_remote` to ship `skill_content` in the body

Find `_agent_run_remote(req)`. Its body dict enumerates fields explicitly. Add `skill_content`:

```python
async def _agent_run_remote(req: _AgentRunRequest) -> dict[str, Any]:
    """POST the request to the VM's vllm-agent serve endpoint."""
    if not VLLM_AGENT_URL:
        return {"status": "error",
                "error": "VLLM_AGENT_URL not set; cannot use mode=remote"}
    body = {
        "task": req.task,
        "skill": req.skill,
        "skill_content": req.skill_content,
        "mode": "remote",
        "workdir": req.workdir,
        "out_dir": req.out_dir,
        "model": req.model,
        "max_iterations": req.max_iterations,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
        "timeout_s": req.timeout_s,
        "extra_context": req.extra_context,
    }
    async with httpx.AsyncClient(timeout=float(req.timeout_s + 30)) as client:
        r = await client.post(f"{VLLM_AGENT_URL}/run", json=body,
                              headers=_agent_headers())
    if r.status_code != 200:
        return {"status": "error", "error": f"VM agent HTTP {r.status_code}: {r.text[:300]}"}
    return r.json()
```

### Step 3: Update `agent_session_start` MCP tool

Same pattern as `agent_run`:

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
    skill_content: str | None = None
    if skill:
        try:
            skill_content = _SkillLoader().load_skill(skill)
        except _SkillNotFound as e:
            return {"status": "error", "error": str(e)}

    req = _AgentSessionStartRequest(
        goal=goal,
        skill=skill,
        skill_content=skill_content,
        mode=mode,
        workdir=workdir,
        model=model,
    )
    if mode == "local":
        return asdict(await _ass_local(req))
    return await _http_session_start({
        "goal": goal,
        "skill": skill,
        "skill_content": skill_content,
        "mode": "remote",
        "workdir": workdir,
        "model": model,
    })
```

### Step 4: Verify imports

Confirm the file imports `_SkillLoader` and `_SkillNotFound` (or under whatever local alias the file uses):

```bash
grep -nE '_SkillLoader|_SkillNotFound|from vllm_agent\.skills' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server/vllm_mcp.py
```

If `_SkillNotFound` isn't imported, add it to the existing skills import. Find the import line that brings in `SkillLoader` and extend it:

```python
from vllm_agent.skills import SkillLoader as _SkillLoader, SkillNotFound as _SkillNotFound
```

(Or the equivalent existing aliasing. Match local convention.)

### Step 5: Smoke test the import + tool registration

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python3 -c "
import vllm_mcp
import inspect
funcs = sorted(n for n,o in inspect.getmembers(vllm_mcp) if inspect.iscoroutinefunction(o) and not n.startswith('_'))
print(f'count: {len(funcs)}')
print('agent_run docstring:', vllm_mcp.agent_run.__doc__[:120] if vllm_mcp.agent_run.__doc__ else '')
"
```

Expected: count=13 (unchanged from Plan F) and the docstring snippet shows the new behavior description.

### Step 6: Local-mode smoke (no VM dependency)

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && \
  VLLM_BASE_URL=http://localhost:8443 \
  VLLM_MODEL=test-model \
  VLLM_AGENT_URL=http://localhost:8443/agent \
  .venv/bin/python -c "
import asyncio
from vllm_mcp import agent_run

async def main():
    # Use a non-existent skill name; expect a clean error from the local SkillLoader.
    out = await agent_run(
        task='x',
        skill='nonexistent:fake-skill',
        mode='local',
        workdir='/tmp',
        out_dir='/tmp/g_smoke_out',
        max_iterations=1,
    )
    print(out)
"
```

Expected: a result dict with `status='error'` and an error string mentioning "Skill not found" (the orchestrator's `SkillNotFound` got caught and surfaced as a clean error). The fact we get this BEFORE any HTTP call confirms the local resolution is happening.

### Step 7: Commit

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan G: MCP shim resolves skill locally and ships content to vllm-agent"
```

---

## Task 3: README — remove the "Skills in remote mode" limitation note

**Files:**
- Modify: `README.md`

### Step 1: Remove the limitation paragraph

In `README.md`, find the paragraph that reads (added in Plan F follow-up):

```markdown
**Skills in remote mode:** the worker container has no access to the
orchestrator's `~/.claude` skill cache today. `agent_run(skill="...", mode="remote")`
will return `SkillNotFound` for skills not bundled into the worker image.
A future plan will mount or sync the skill content into the container.
For now, use `extra_context` to pass skill text directly, or run skill-bound
work in `mode="local"` where the orchestrator's skill cache is reachable.
```

Delete the entire paragraph including the `**Skills in remote mode:**` lead-in. Leave the surrounding sections (URL layout table above, Mode-selection guidance below) untouched.

### Step 2: Verify the limitation note is gone

```bash
grep -n 'Skills in remote mode' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/README.md
```

Expected: zero matches.

### Step 3: Commit

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add README.md
git commit -m "Plan G: remove Skills-in-remote-mode limitation note (now resolved)"
```

---

## Task 4: Live dogfood — sync source to VM, restart vllm-agent, end-to-end test

This task validates the change against the live VM at `192.168.9.168` (the Plan F deploy). No VM reprovision needed — the vllm-agent container has the source bind-mounted from the VM's filesystem, so a `docker compose restart vllm-agent` after pushing the new code is enough.

**Files:** none modified by this task

### Step 1: Confirm VM is alive and current

```bash
curl -sf http://192.168.9.168:8443/health && echo
```

Expected: `{"ok":true,"vllm_base_url":"http://vllm:8000","vllm_model":"QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ"}`. If unreachable, the previous deploy is gone — fall back to Step 2 of the launch script (full reprovision) or skip this dogfood.

### Step 2: Sync the updated source tree into the VM

The launch script's existing tar/scp/lxc-file-push flow is exactly what we want, but we don't need the VM-init steps. Run a one-liner:

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev && \
TARBALL="$(mktemp -t rtx_5090_dev.XXXXXX.tar.gz)" && \
tar --exclude='./.git' --exclude='./.worktrees' \
    --exclude='./vllm-agent/.venv' --exclude='./vllm-agent/.venv-agent' \
    --exclude='./mcp-server/.venv' --exclude='./test_apps' \
    --exclude='**/__pycache__' --exclude='**/*.egg-info' \
    -C . -czf "$TARBALL" . && \
scp -q "$TARBALL" bdx@192.168.7.11:/tmp/rtx_5090_dev.tar.gz && \
rm -f "$TARBALL" && \
ssh bdx@192.168.7.11 "lxc file push /tmp/rtx_5090_dev.tar.gz rtx5090/tmp/rtx_5090_dev.tar.gz && lxc exec rtx5090 -- bash -c 'cd /home/ubuntu/rtx_5090_dev && tar -xzf /tmp/rtx_5090_dev.tar.gz && chown -R ubuntu:ubuntu /home/ubuntu/rtx_5090_dev && rm /tmp/rtx_5090_dev.tar.gz'" && \
ssh bdx@192.168.7.11 "lxc exec rtx5090 -- rm /tmp/rtx_5090_dev.tar.gz 2>/dev/null || true"
```

(`tar -xzf` here OVERWRITES files in the existing checkout with new versions. Bind-mount-aware: the running container's `/app` IS this directory.)

### Step 3: Restart vllm-agent container to pick up the new code

```bash
ssh bdx@192.168.7.11 -- 'lxc exec rtx5090 -- su - ubuntu -c "cd ~/rtx_5090_dev && docker compose restart vllm-agent"' 2>&1 | tail -3
```

Expected: `Container rtx_5090_dev-vllm-agent-1 Started` or similar. The container's `command:` re-runs `pip install --user --no-warn-script-location -e /app`, picking up the new `skill_content` field (~10s).

### Step 4: Wait for vllm-agent to be ready

```bash
KEY=$(python3 -c "import json; print(json.load(open('/home/bdx/allcode/github/vantagecompute/rtx_5090_dev/.mcp.json'))['mcpServers']['vllm-rtx5090']['env']['VLLM_AGENT_API_KEY'])")
for i in $(seq 1 30); do
  if curl -sf -H "Authorization: Bearer $KEY" --max-time 3 http://192.168.9.168:8443/agent/skills | grep -q '\['; then
    echo "vllm-agent ready (attempt $i)"
    break
  fi
  echo "  attempt $i: not ready"
  sleep 2
done
```

Expected: `vllm-agent ready` within ~10 attempts.

### Step 5: End-to-end test from the orchestrator side

This step runs `agent_run` through the local mcp-server's Python (since Claude Code's MCP server still has the OLD env vars baked in until the user restarts; testing via direct Python is the same code path):

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && \
  VLLM_BASE_URL=http://192.168.9.168:8443 \
  VLLM_MODEL=QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ \
  VLLM_AGENT_URL=http://192.168.9.168:8443/agent \
  VLLM_AGENT_API_KEY=$(python3 -c "import json; print(json.load(open('/home/bdx/allcode/github/vantagecompute/rtx_5090_dev/.mcp.json'))['mcpServers']['vllm-rtx5090']['env']['VLLM_AGENT_API_KEY'])") \
  .venv/bin/python -c "
import asyncio, json
from vllm_mcp import agent_run, agent_run_artifacts

async def main():
    r = await agent_run(
        task='Following the test-driven-development skill, write /tmp/g_dogfood/double.py with a function double(x) that returns x*2. Verify with python3 -m py_compile.',
        skill='superpowers:test-driven-development',
        mode='remote',
        workdir='/tmp/g_dogfood',
        out_dir='/tmp/g_dogfood_run',
        max_iterations=15,
        max_tokens=4096,
        temperature=0.1,
    )
    print('agent_run status:', r['status'])
    print('iterations:', r['iterations'])
    print('files_changed:', r['files_changed'])

    a = await agent_run_artifacts(out_dir='/tmp/g_dogfood_run', mode='remote', tail_lines=50)
    print()
    print('=== summary ===')
    print(a['summary'])

    # Verify the skill text actually made it into the system prompt
    sys_msgs = [r for r in a['transcript_tail'] if r.get('kind') == 'message' and r.get('role') == 'system']
    if sys_msgs:
        sys_content = sys_msgs[0]['content']
        if 'test-driven' in sys_content.lower() or 'TDD' in sys_content or 'red, green' in sys_content.lower():
            print()
            print('✓ TDD skill content found in system prompt')
        else:
            print()
            print('✗ TDD skill content NOT in system prompt (first 300 chars):', sys_content[:300])

asyncio.run(main())
" 2>&1 | grep -v 'INFO.*HTTP Request' | tail -25
```

Expected:
- `agent_run status: ok`
- `iterations: ` ≥ 1
- `files_changed:` includes `double.py` (and possibly `__pycache__/double.cpython-312.pyc` from py_compile)
- The summary mentions running `py_compile` (verify-before-finish discipline kicks in)
- `✓ TDD skill content found in system prompt` (the skill markdown made it into the worker's prompt)

If the system-prompt check fails, the skill resolution path isn't working — check that the orchestrator's `~/.claude/plugins/cache/claude-plugins-official/superpowers/*/skills/test-driven-development/SKILL.md` exists.

### Step 6: Workdir setup on VM (if needed)

The workdir `/tmp/g_dogfood` must exist on the VM (and be writable by the container's uid 1000). If the test fails with a workdir error:

```bash
ssh bdx@192.168.7.11 -- 'lxc exec rtx5090 -- bash -c "mkdir -p /tmp/g_dogfood && chown 1000:1000 /tmp/g_dogfood"' 2>&1
```

Then re-run Step 5.

### Step 7: No new commit

This task validates; nothing to commit.

---

## Final verification

- `git log --oneline b2ab787..HEAD` shows commits from Tasks 1-3 (3 commits).
- `cd vllm-agent && uv run pytest -v` returns 83 passing (1 deselected).
- vllm_mcp imports cleanly with 13 async tools.
- Live dogfood: `agent_run(skill="superpowers:test-driven-development", mode="remote")` succeeds AND the system-prompt check confirms skill content delivery.

---

## Self-Review

### Spec coverage

| Spec section | Tasks |
|---|---|
| §2 Architecture (orchestrator-side resolution + content handoff) | Task 1 (worker side) + Task 2 (orchestrator side) |
| §3 `AgentRunRequest.skill_content` | Task 1 Step 3 |
| §3 `AgentSessionStartRequest.skill_content` | Task 1 Step 3 |
| §3 `agent_run` skill resolution if-elif-else | Task 1 Step 4 |
| §3 `Session.skill_content` + persistence | Task 1 Step 5, 6 |
| §3 `agent_session_step` reuse of persisted content | Task 1 Step 7 |
| §3 Pydantic body models with `skill_content` | Task 1 Step 8 |
| §3 mcp-server resolves skill locally (2 tools) | Task 2 Steps 1, 3 |
| §3 `_agent_run_remote` ships `skill_content` in body | Task 2 Step 2 |
| §3 `_http_session_start` ships `skill_content` in body | Task 2 Step 3 (inline in `agent_session_start`) |
| §4 Offline tests (3 new vllm-agent tests) | Task 1 Step 1 |
| §4 Live dogfood | Task 4 |
| §4 Doc update (remove limitation note) | Task 3 |

### Placeholder scan

No "TBD", "TODO", "fill in details" tokens. All code blocks contain complete content. Two soft phrases I deliberately kept:

- Task 1 Step 6: "find ALL calls to `store.create(...)`" — implementer must locate them. This is a navigational hint, not a placeholder; the kwargs format is fully specified in the same step.
- Task 2 Step 4: "Match local convention" for the import alias — refers to the existing import pattern in `vllm_mcp.py`. The grep command in the same step locates the existing imports.

### Type / signature consistency

- `skill_content: str | None = None` consistent across `AgentRunRequest`, `AgentSessionStartRequest`, `Session`, `RunBody`, `SessionStartBody`.
- `SessionStore.create(*, goal, skill, skill_content, mode, workdir, model)` — kwarg-only signature; all 4 call sites (3 tests, 1 in `agent_session_start`) use the same kwarg order.
- `_AgentRunRequest`, `_AgentSessionStartRequest`, `_SkillLoader`, `_SkillNotFound` — local aliases used in `mcp-server/vllm_mcp.py`. Plan assumes existing import convention; Step 4 of Task 2 verifies and adds the `_SkillNotFound` alias if missing.

No issues found.
