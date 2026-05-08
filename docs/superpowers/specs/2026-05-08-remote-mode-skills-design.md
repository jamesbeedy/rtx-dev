# Remote-Mode Skills — Design

Date: 2026-05-08
Status: Approved (brainstorming phase)
Next step: writing-plans → implementation plan

---

## 1. Goal & Non-Goals

### Goal

Make `agent_run(skill="superpowers:test-driven-development", mode="remote")`
actually load the skill content for the worker. Today the worker container
has no access to the orchestrator's `~/.claude` skill cache, so any
`agent_run(skill=NAME, mode="remote")` call returns `SkillNotFound`. Fix by
resolving the skill content on the orchestrator side and shipping the
markdown content in the request body.

The orchestrator's local `SkillLoader` is the single source of truth. The
worker is dumb about skills — it just receives ready-to-use prompt text
when the orchestrator wants to bind a skill, otherwise its `_WORKER_TAIL`
prompt runs alone.

### Non-Goals

- TLS termination on `:8443`. Still HTTP. Future plan.
- HF model cache persistence across VM reprovision. Still ephemeral. Future
  plan.
- Skill discovery from inside the VM. `list_skills` continues to read the
  orchestrator's local cache via the MCP shim's local `SkillLoader`; this
  is correct.
- Any change to `mode=local` behavior. It already works because the
  worker shares the orchestrator's filesystem.
- Any change to the worker's system-prompt budget enforcement, transcript
  recording, or session storage layout — the new field is appended; existing
  behavior is unchanged.

---

## 2. Architecture

```
mode=local (unchanged):
  Claude → mcp__vllm-rtx5090__agent_run(skill="superpowers:tdd")
        → vllm_mcp.agent_run        (in mcp-server process)
        → vllm_agent.api.agent_run  (in-process, same fs)
        → SkillLoader.load_skill(name)   ← reads ~/.claude on local fs ✓

mode=remote (broken today; fixed by this spec):
  Claude → mcp__vllm-rtx5090__agent_run(skill="superpowers:tdd", mode="remote")
        → vllm_mcp.agent_run            (in mcp-server process)
        → ★ NEW: resolve skill locally via SkillLoader.load_skill(name)
        → ★ NEW: pass `skill_content=<full markdown>` in HTTP body
        → POST {VLLM_AGENT_URL}/run
        → vllm-agent server.py /run     (in VM container)
        → vllm_agent.api.agent_run(req) (req.skill_content is set)
        → ★ NEW: if req.skill_content set → use directly, skip SkillLoader
        → if req.skill set but no content → fall back to SkillLoader
          (works in mode=local; surfaces a clear error in mode=remote)
```

The seam is a single new field — `skill_content: str | None` — on
`AgentRunRequest` and `AgentSessionStartRequest`. The orchestrator-side
MCP shim fills it; the worker-side `agent_run` consumes it.

### Backward compatibility

- Old callers passing only `skill="..."` continue to work in `mode=local`.
- Old `mode=remote` callers without `skill_content` get the SAME error
  message they get today (`SkillNotFound`). New callers via the MCP shim
  get the resolution-on-the-orchestrator transparently — no caller change
  required.
- Old `session.json` files without `skill_content` are read fine: the field
  defaults to `None` via the dataclass.

---

## 3. Concrete API + code changes

### `vllm-agent/src/vllm_agent/api.py`

Add `skill_content` to two dataclasses:

```python
@dataclass
class AgentRunRequest:
    task: str
    skill: str | None = None
    skill_content: str | None = None       # NEW
    mode: str = "remote"
    workdir: str | None = None
    out_dir: str | None = None
    model: str | None = None
    max_iterations: int = 30
    max_tokens: int = 4096
    temperature: float = 0.2
    timeout_s: int = 1800
    extra_context: list[str] | None = None


@dataclass
class AgentSessionStartRequest:
    goal: str
    skill: str | None = None
    skill_content: str | None = None       # NEW
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None
```

In `agent_run` and `agent_session_step`, change the skill-resolution lookup:

```python
# Before:
skill_content = SkillLoader().load_skill(req.skill) if req.skill else None

# After:
if req.skill_content:
    skill_content = req.skill_content
elif req.skill:
    skill_content = SkillLoader().load_skill(req.skill)
else:
    skill_content = None
```

For `agent_session_step`, the resolution happens once on the FIRST step
(by reading `session.skill_content` if set, else falling back as above).
Subsequent steps reuse the persisted value.

### `vllm-agent/src/vllm_agent/sessions.py`

`Session` gains the field; `SessionStore.create()` accepts it as a kwarg
and persists it in `session.json`:

```python
@dataclass
class Session:
    session_id: str
    goal: str
    skill: str | None
    skill_content: str | None         # NEW
    mode: str
    workdir: str
    model: str | None
    status: SessionStatus
    started_at: float
    last_activity_at: float
    iterations_total: int = 0
    files_changed_total: list[str] = field(default_factory=list)


# in SessionStore.create:
def create(self, goal: str, skill: str | None,
           skill_content: str | None,
           mode: str, workdir: str, model: str | None) -> Session:
    ...
```

### `vllm-agent/src/vllm_agent/server.py`

Add the field to two Pydantic body models so it deserializes from JSON:

```python
class RunBody(BaseModel):
    task: str
    skill: str | None = None
    skill_content: str | None = None       # NEW
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
    skill_content: str | None = None       # NEW
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None
```

The endpoint handlers already do `AgentRunRequest(**body.model_dump())`
and `AgentSessionStartRequest(**body.model_dump())` — the new field flows
through automatically.

### `mcp-server/vllm_mcp.py`

`agent_run` and `agent_session_start` MCP tools resolve the skill locally
before dispatching:

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
    """..."""
    skill_content: str | None = None
    if skill:
        try:
            skill_content = _SkillLoader().load_skill(skill)
        except _SkillNotFound as e:
            return {"status": "error", "error": str(e)}

    req = _AgentRunRequest(
        task=task, skill=skill, skill_content=skill_content,
        mode=mode, workdir=workdir, out_dir=out_dir,
        model=model, max_iterations=max_iterations,
        max_tokens=max_tokens, temperature=temperature,
        timeout_s=timeout_s, extra_context=extra_context,
    )
    if mode == "local":
        return asdict(await _agent_run_local(req))
    return await _agent_run_remote(req)
```

`_agent_run_remote` already enumerates fields into the POST body — add
`"skill_content": req.skill_content` to that dict.

Same pattern for `agent_session_start` (calls `_SkillLoader().load_skill`,
passes `skill_content` into the request) and `_http_session_start` (adds
`skill_content` to the POST body).

The local `_SkillLoader` import (and `_SkillNotFound`) already exists in
`vllm_mcp.py` (used by the `list_skills` tool); reuse it.

### Tests

#### `vllm-agent/tests/test_api.py`

```python
@respx.mock
async def test_agent_run_uses_skill_content_when_provided(tmp_path, monkeypatch):
    """If skill_content is set, the worker prompt includes it; SkillLoader is not called."""
    captured: dict = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_capture)
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    req = AgentRunRequest(
        task="anything", skill="ignored:name",
        skill_content="--- PROVIDED SKILL CONTENT ---",
        mode="remote", workdir=str(tmp_path), out_dir=str(tmp_path / "out"),
        max_iterations=1, max_tokens=64,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    # The worker's system message in the request body contains the skill content.
    sys_msg = next(m for m in captured["body"]["messages"] if m["role"] == "system")
    assert "PROVIDED SKILL CONTENT" in sys_msg["content"]
```

#### `vllm-agent/tests/test_server.py`

```python
@respx.mock
def test_run_endpoint_accepts_skill_content(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    captured: dict = {}
    def _capture(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_capture)

    r = client.post("/run", json={
        "task": "x", "skill": "fake:skill",
        "skill_content": "--- INJECTED ---",
        "mode": "remote", "workdir": str(tmp_path),
        "out_dir": str(tmp_path / "out"),
        "max_iterations": 1, "max_tokens": 64,
    })
    assert r.status_code == 200
    sys_msg = next(m for m in captured["body"]["messages"] if m["role"] == "system")
    assert "INJECTED" in sys_msg["content"]
```

#### `vllm-agent/tests/test_sessions.py`

```python
def test_session_persists_skill_content(tmp_path):
    store = SessionStore(root=tmp_path)
    s = store.create(goal="g", skill="x", skill_content="X content",
                     mode="local", workdir="/tmp", model=None)
    s2 = store.load(s.session_id)
    assert s2.skill_content == "X content"
```

Existing tests pass unchanged because all `SessionStore.create` calls in
the existing test suite would need a new positional/kwarg arg. That means
the change requires updating those calls too — at least 4 sites in
`test_sessions.py` and `test_api.py` to add `skill_content=None`. Spec
note: when implementing, add `skill_content` BEFORE `mode` if positional,
or use kwargs throughout to avoid argument-order pain.

---

## 4. Testing + rollout

### Offline checks

- `cd vllm-agent && uv run pytest -v` — current 79 PASS → ~82 PASS with the
  3 new tests covering the `skill_content` path. Existing tests that call
  `SessionStore.create` need their signatures updated for the new
  `skill_content` arg; if any are missed, they'll fail with TypeError and
  surface immediately.
- `python3 -c "import vllm_mcp; print('OK')"` after the shim changes.

### Live check (against the running Plan F VM)

The current VM at `192.168.9.168` is a Plan F deploy — vllm-agent runs in
a container with the source bind-mounted from
`/home/ubuntu/rtx_5090_dev/vllm-agent`. After landing the code:

1. Sync source: `scp -r vllm-agent/src/vllm_agent/ bdx@192.168.7.11:/tmp/`
   then `lxc file push -r /tmp/vllm_agent rtx5090/home/ubuntu/rtx_5090_dev/vllm-agent/src/`
   (or just re-tar+scp+lxc-file-push the whole repo).
2. `lxc exec rtx5090 -- su - ubuntu -c 'cd ~/rtx_5090_dev && docker compose restart vllm-agent'`
3. Restart Claude Code so the local mcp-server picks up the shim changes.
4. From Claude:
   ```
   agent_run(task="Following the TDD skill, write a function that reverses a string.",
             skill="superpowers:test-driven-development",
             mode="remote",
             workdir="/tmp/skill_e2e",
             out_dir="/tmp/skill_e2e_run",
             max_iterations=10, max_tokens=2048)
   ```
5. Fetch via `agent_run_artifacts`. Verify the transcript's first system
   message contains the TDD skill markdown (the `# Test-Driven Development`
   header, the rigid TDD checklist, etc.).

Total turnaround: ~30 seconds, no VM reprovision, no model redownload.

### Rollout order

1. **Land `vllm-agent` changes** (api.py + sessions.py + server.py + 3 new
   tests + signature fixes for existing tests). Single commit.
2. **Land `mcp-server` changes** (resolve skill locally in 2 MCP tools,
   add `skill_content` to 2 HTTP body builders). Single commit.
3. **README/CLAUDE.md** — remove the "Skills in remote mode" limitation
   note. Single commit.
4. **Live dogfood** — source-sync to VM + container restart + e2e.

3 commits + 1 dogfood task.

---

## Constraints & Risks

- **Skill content can be large.** Some superpowers skills (brainstorming,
  systematic-debugging) are 5–10 KB. The orchestrator-side resolution
  embeds this in the POST body. Network impact: negligible. Worker-side:
  the existing `prompts.build_system_prompt(budget_chars=DEFAULT_BUDGET_CHARS)`
  already enforces a 96 KB ceiling and will raise `PromptBudgetError` if a
  skill blows past it. No new budget checks needed.
- **Backward compat with sessions on disk.** Old `session.json` files
  written before this change don't have `skill_content`. The dataclass
  default of `None` handles this — sessions created before the change
  continue to load.
- **`SessionStore.create` signature changes** add a positional arg. All
  existing callers need updating (in tests and in `agent_session_start`).
  Easy to enumerate via `git grep`. Implementer should use kwargs
  consistently to avoid future arg-order drift.
- **Authorization remains unchanged** — the new field rides inside the
  already-authenticated POST body. Plan D's API key still gates
  `/run` and `/session`.
- **No risk to mode=local.** All changes are additive; `mode=local` paths
  short-circuit before any HTTP serialization.
