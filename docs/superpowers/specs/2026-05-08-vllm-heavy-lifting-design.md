# vllm-rtx5090 as a Near-Peer Coding Agent — Design

Date: 2026-05-08
Status: Approved (brainstorming phase)
Next step: writing-plans → implementation plan

---

## 1. Goal & Non-Goals

### Goal

Turn the `vllm-rtx5090` MCP server into a generic dispatch surface for a coding
agent backed by `Qwen3-Coder-30B-A3B-Instruct-AWQ` (or any future vLLM model),
so that Claude Code can offload:

- Bulk code generation (whole-file/whole-module drafts, scaffolds, refactors)
- Long-context analysis (summaries, reviews, audits over large files/dirs)
- Iterative agentic loops where the worker writes code, runs tests, fixes
  failures, and reports back without consuming Claude turns per step

The user-stated framing: a "near-peer coding agent" where Claude orchestrates
and reviews while vllm-rtx5090 does most of the actual generation/iteration.

### Non-Goals

- Making vllm match Opus on hard reasoning or design judgment. Claude stays
  the orchestrator and reviewer.
- Sneaking generated content into Claude's context. The current output
  discipline (write to disk, return metadata) is preserved.
- Inventing a new agent framework. We borrow patterns (skill-as-system-prompt,
  tool-call loop) but write our own minimal runtime.

---

## 2. Architecture

Three layers:

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Code (orchestrator)                                  │
│    sees: agent_run, agent_session_*, health,                 │
│          list_models, list_skills                            │
└──────────────────────┬──────────────────────────────────────┘
                       │  MCP (stdio)
┌──────────────────────▼──────────────────────────────────────┐
│  mcp-server/vllm_mcp.py  (thin shim, ~150 lines)             │
│    - exposes MCP tools                                       │
│    - dispatches to vllm_agent: in-proc (local) or HTTP (VM)  │
│    - existing tools (ask, scaffold, critique, converse)      │
│      become thin wrappers over the new runtime               │
└──────────────────────┬──────────────────────────────────────┘
                       │ Python import (local) │ HTTP (VM)
┌──────────────────────▼────────────────────────▼─────────────┐
│  vllm_agent/  (new standalone Python package)                │
│    - agent loop (vLLM chat-completions + tool-call loop)     │
│    - worker palette: read, edit, write, bash, grep, glob,    │
│      web_search, finish                                      │
│    - skill loader, workspace manager, session store          │
│    - CLI: `vllm-agent run --skill X --task ...`              │
│    - HTTP API: POST /run, /session, /session/{id}/step       │
└──────────────────────────────────────────────────────────────┘
```

### Execution Modes

Mode is per-call, picked by Claude (or by the human nudging Claude).

#### `mode=local`

- `mcp-server` imports `vllm_agent` and runs the loop in-process.
- Worker tools (Read/Edit/Bash) execute **on the user's machine**.
- Worker has unrestricted `Bash(*)` and edits files directly in `workdir`.
- `workdir` defaults to Claude's CWD; explicitly settable per call.
- **No sandboxing.** This risk is accepted by the user as an explicit choice.
- The vLLM endpoint is still the remote VM — only the agent loop and tool
  execution are local.

**Safety opt-in (default-off):** local Bash is gated by environment variable
`VLLM_AGENT_LOCAL_BASH=1`. When unset, `mode=local` calls fail with a clear
error message that points the caller to set the env var. This guards against
an inadvertent local-bash run if the MCP server is ever installed elsewhere.

#### `mode=remote`

- `mcp-server` POSTs to `vllm-agent serve` running inside the GPU VM.
- Worker tools execute **inside the VM** on a copy of the repo synced via
  git push/fetch.
- Default sync model: shim pushes the current branch to the VM as
  `agent/<run-id>`, the worker writes commits onto that branch, and the
  shim fetches it back so Claude can `git diff agent/<run-id>` to review.
- Same unrestricted Bash, but in the disposable VM (the VM is the sandbox).

#### Mode selection guidance (for CLAUDE.md)

- Short, read-heavy or generate-only tasks: `local` is fine.
- Long autonomous sessions, anything destructive, or anything that runs
  package installs / network calls: prefer `remote`.
- The MCP server does not auto-pick. The caller chooses.

---

## 3. MCP Tool Surface

Seven tools that Claude sees. Generic, parameterized by skill.

### Utility

```
health() -> {ok, endpoint, model, modes_available,
             default_system_set, local_bash_enabled,
             remote_agent_url|None}

list_models() -> [str]   # unchanged from current

list_skills() -> [{name, source, path, description}]
    # Walks configured skill roots (see §5), returns frontmatter metadata.
```

### Agent dispatch

```
agent_run(
    task: str,                  # the actual user-facing instruction
    skill: str | None = None,   # e.g. "superpowers:test-driven-development"
    mode: "local" | "remote" = "remote",
    workdir: str | None = None, # default: CWD (local) or repo root (remote)
    out_dir: str | None = None, # default: ~/.cache/vllm-agent/runs/<run_id>/
    model: str | None = None,
    max_iterations: int = 30,   # tool-call loop cap
    max_tokens: int = 4096,     # per vLLM call
    temperature: float = 0.2,
    timeout_s: int = 1800,      # 30 min default
    extra_context: list[str] | None = None,  # list of file paths to inline into the worker's prompt
) -> {
    "run_id": str,
    "out_dir": str,             # transcript.jsonl, summary.md, files_changed.txt
    "summary_path": str,
    "files_changed": [str],     # relative to workdir
    "diff_path": str | None,    # written if mode=local or fetched if mode=remote
    "iterations": int,
    "search_log": [...],
    "duration_s": float,
    "status": "ok" | "max_iterations" | "timeout" | "error",
    "error": str | None,
}
```

### Long-running sessions

```
agent_session_start(
    goal: str,
    skill: str | None = None,
    mode: "local" | "remote" = "remote",
    workdir: str | None = None,
    model: str | None = None,
) -> {"session_id": str, "out_dir": str, "status": "running"}

agent_session_step(
    session_id: str,
    nudge: str | None = None,   # optional injected user message
    max_iterations: int = 10,
) -> {
    "session_id": str,
    "iterations_this_step": int,
    "files_changed_this_step": [str],
    "summary_path": str,
    "status": "running" | "ok" | "max_iterations" | "timeout" | "error",
}

agent_session_status(session_id: str) -> {
    "session_id": str,
    "status": "running" | "stopped" | "completed" | "errored",
    "iterations_total": int,
    "files_changed_total": [str],
    "started_at": str,
    "last_activity_at": str,
    "out_dir": str,
}

agent_session_stop(session_id: str) -> {"session_id": str, "status": "stopped"}
```

### Output discipline

`agent_run` and `agent_session_step` never return raw model output to Claude.
Everything goes to `out_dir`:

- `transcript.jsonl` — full message history with tool calls
- `summary.md` — the worker writes a final summary as its last action
- `files_changed.txt` — list of paths the worker touched
- `diff.patch` — `git diff` of the worker's changes

Claude gets back **paths and metadata only**. If Claude wants the actual
content, it `Read`s the file itself.

### Key invariants

- Every `agent_*` call works the same way regardless of `mode`. The `mode`
  flag only affects where the loop runs, not the tool surface.
- No concurrent `agent_session_step` on the same session. Second call while
  first is in flight returns `{status: "running", error: "step in progress"}`.

---

## 4. Worker Loop & Tool Palette

The agent loop inside `vllm_agent` generalizes the existing `_generate()`
pattern in `vllm_mcp.py`.

### Loop pseudocode

```python
msgs = [
  {role: "system", content: build_system_prompt(skill, palette, workdir)},
  {role: "user",   content: build_user_prompt(task, extra_context)},
]
for i in range(max_iterations):
    resp = vllm.chat(msgs, tools=WORKER_TOOLS, tool_choice="auto",
                     max_tokens=..., temperature=...)
    msg = resp.choices[0].message
    msgs.append(assistant(msg))
    if not msg.tool_calls:
        break                              # final answer
    for tc in msg.tool_calls:
        result = WORKER_TOOLS[tc.name].execute(tc.args, workspace)
        msgs.append(tool_result(tc.id, result))
        record_to_transcript(...)
        if budget_exceeded(): break
```

### Worker tool palette

These are the tools vllm itself can call inside its loop — distinct from
the MCP tools Claude sees.

| Tool | Purpose | Notes |
|------|---------|-------|
| `read_file(path, offset?, limit?)` | Read a file | Mirrors Claude's `Read` |
| `write_file(path, content)` | Write/overwrite | New file or full rewrite |
| `edit_file(path, old, new, replace_all?)` | Targeted edit | Mirrors Claude's `Edit` — exact-string match |
| `bash(command, cwd?, timeout_s?)` | Run any shell command | **Unrestricted.** `cwd` defaults to workdir. |
| `grep(pattern, path?, glob?, ...)` | Ripgrep wrapper | Returns matches with line numbers |
| `glob(pattern, path?)` | Find files by pattern | |
| `web_search(query)` | Existing DDG search | Unchanged from current `vllm_mcp.py` |
| `finish(summary)` | Signal done with a summary | Worker calls this last; summary written to `summary.md` |

### System-prompt construction (for `agent_run`)

```
[skill content if skill name passed — full SKILL.md verbatim]

You are the vllm-rtx5090 worker. You have these tools:
  read_file, write_file, edit_file, bash, grep, glob, web_search, finish

Workspace: {workdir}
Mode: {local|remote}

Discipline:
- Edit files in place via edit_file/write_file. Run tests via bash.
- Iterate until the task is done or you hit a blocker.
- When done, call finish() with a 1-2 paragraph summary of what you did,
  what you changed, and anything the orchestrator should verify.
- Never edit files outside {workdir}.
- Use web_search for facts you don't reliably know.
```

### Skill-as-system-prompt details

- `list_skills()` returns name → path. `agent_run(skill="superpowers:test-driven-development", ...)`
  reads that file's content and prepends it to the system prompt.
- For project-local skills under `./skills/`, same pattern applies.
- If `skill content + tools schema + extra_context` exceeds ~24k tokens
  (leaving 8k for completion within the 32k vllm context limit), we
  truncate `extra_context` first, then warn in the result.
- Supporting files referenced by a skill (e.g. `references/foo.md`) are
  **not** auto-loaded. The skill prompt itself decides whether to instruct
  the worker to `read_file` them, otherwise we'd blow the context budget
  on every call.

### Telemetry per call

- `iterations`: number of vllm round-trips
- `tool_calls`: count by tool name
- `bytes_read` / `bytes_written` / `bash_invocations`
- `search_log`: queries issued (preserved from current design)
- `transcript.jsonl`: full record for forensic debugging

### Failure handling

- Tool exceptions become `{"error": ...}` tool results; the worker sees them
  and can adjust.
- vLLM API errors (timeout, 5xx) get one retry with backoff, then surface as
  `status: "error"`.
- If the worker calls `finish()` with no summary, we synthesize a placeholder
  and flag it in `warnings`.
- If the worker hits `max_iterations` without calling `finish()`, the run
  returns `status: "max_iterations"` and the partial transcript is preserved.

---

## 5. Sessions, Skill Loading, Project Layout

### Session storage

Sessions are persisted agent state under
`~/.cache/vllm-agent/sessions/<session_id>/`:

```
session.json        # {goal, skill, mode, workdir, model, status, started_at, ...}
messages.jsonl      # full message history (resumed each step)
transcript.jsonl    # tool-call record across all steps
summary.md          # latest finish() summary, overwritten each step
files_changed.txt   # union across steps
```

### Session lifecycle

- `agent_session_start(goal, skill, mode, workdir)` — creates the directory,
  writes `session.json`, returns `session_id`. Doesn't run any vllm calls;
  the first step does.
- `agent_session_step(session_id, nudge?, max_iterations=10)` — loads
  `messages.jsonl`, appends the nudge (if any) as a `user` message, runs the
  loop for up to `max_iterations` rounds, persists messages, returns
  metadata. Step boundaries let Claude inspect intermediate state and
  intervene.
- `agent_session_status(session_id)` — pure read.
- `agent_session_stop(session_id)` — sets status to `"stopped"`, no-ops
  further `step` calls.

### Remote sessions

State lives on the VM under `/var/lib/vllm-agent/sessions/<id>/`. The MCP
shim's `agent_session_*` calls become HTTP calls; the VM holds the state.
The shim caches the `session_id → mode` mapping locally so it knows where
to route subsequent calls.

### Skill discovery

Skills come from configured roots, walked in order, first match wins:

```python
SKILL_ROOTS = [
    Path("./skills"),                                                # project-local
    Path("~/.claude/skills").expanduser(),                           # user
    Path("~/.claude/plugins/cache/claude-plugins-official").expanduser(),  # superpowers
]
```

`list_skills()` walks each root, reads frontmatter from every `SKILL.md`
(or `.md` with skill-shaped frontmatter), and returns:

```json
[
  {"name": "superpowers:test-driven-development",
   "source": "superpowers", "path": "...", "description": "..."},
  {"name": "project:my-custom-flow",
   "source": "project", "path": "./skills/my-custom-flow/SKILL.md", "description": "..."}
]
```

Naming convention: `<source>:<skill-name>`.

### Project layout (after refactor)

```
rtx_5090_dev/
├── mcp-server/
│   ├── pyproject.toml
│   └── vllm_mcp.py              # ~150 lines: just MCP shim
├── vllm-agent/                  # new package
│   ├── pyproject.toml
│   └── src/vllm_agent/
│       ├── __init__.py
│       ├── loop.py              # tool-call loop
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── fs.py            # read_file, write_file, edit_file, grep, glob
│       │   ├── shell.py         # bash
│       │   ├── search.py        # web_search (DDG, ported from current)
│       │   └── finish.py
│       ├── skills.py            # list_skills, load_skill
│       ├── workspace.py         # workdir resolution + path checks
│       ├── sessions.py          # session storage
│       ├── prompts.py           # system-prompt construction
│       ├── server.py            # FastAPI HTTP API for `vllm-agent serve`
│       └── cli.py               # `vllm-agent run`, `vllm-agent serve`
├── profiles/
│   └── rtx-inference.yaml.tpl   # updated to also install vllm-agent in VM
├── launch-inference.sh          # updated to start `vllm-agent serve` in VM
├── .mcp.json                    # gains VLLM_AGENT_URL, VLLM_AGENT_LOCAL_BASH
└── README.md                    # updated
```

### Backward compatibility

Existing `ask` / `scaffold` / `critique` / `converse` tools stay in
`vllm_mcp.py` for backward compatibility. Their bodies become thin wrappers
that call into `vllm_agent.loop` with hardcoded skills/system-prompts.
Nothing changes for current callers.

---

## 6. Testing & Rollout

### Testing strategy

The runtime splits cleanly into testable units. TDD-friendly per the
superpowers `test-driven-development` skill (we apply it to ourselves).

#### Unit tests (no vLLM, no network, fast)

- `tools/fs.py` — read/write/edit/grep/glob against `tmp_path` fixtures.
  Edge cases: missing files, edit `old_string` not unique, path resolution.
- `tools/shell.py` — `bash` against simple commands; capture stdout/stderr/exit;
  timeout behavior; cwd handling.
- `skills.py` — fixture skill roots, frontmatter parsing, name resolution,
  ordering precedence, missing skills.
- `workspace.py` — workdir defaults, path checks (the soft "stay in workdir"
  rule, even though Bash can break it).
- `sessions.py` — start/step/status/stop with mocked loop; persistence across
  "process restarts" (re-instantiating from disk).
- `prompts.py` — system-prompt assembly with/without skill, with/without
  extra_context, context-budget truncation.

#### Integration tests (mock the vLLM HTTP endpoint with `respx`)

- `loop.py` — full tool-call loop with scripted vLLM responses: single-shot
  finish, 5-iteration loop, max_iterations cap, tool-error recovery,
  vLLM 5xx retry.
- `agent_run` end-to-end with a mocked vLLM serving canned tool-call
  sequences against a real `tmp_path` workspace.
- `agent_session_*` end-to-end with mocked vLLM, verifying state persists
  between steps.

#### Live smoke tests (hit the real VM, opt-in via `pytest -m live`)

- `health` works
- `agent_run(task="echo hello via bash and finish", mode=remote)` completes
  in <30s
- `agent_run(task="...", skill="superpowers:test-driven-development", mode=remote)`
  against a tiny fixture repo — verify it actually runs tests and iterates
- Session start/step/stop happy path

#### MCP-level test

Spin up the `mcp-server` process and call its tools via the `mcp` Python
client; verify the 7 tool surfaces match this spec.

### Rollout (in order)

1. **Land `vllm-agent` package** with full unit test coverage. CLI works
   locally against the VM's vLLM endpoint. No MCP changes yet.
2. **Add `vllm-agent serve` HTTP API** + integration tests against a mocked
   vLLM. Still no MCP changes.
3. **Update `profiles/rtx-inference.yaml.tpl` + `launch-inference.sh`** to
   install `vllm-agent` in the VM and run `vllm-agent serve` as a systemd
   unit alongside vLLM. Verify health endpoint after a fresh provision.
4. **Refactor `vllm_mcp.py`** to delegate `ask` / `scaffold` / `critique` /
   `converse` into `vllm_agent.loop` (no behavior change — existing tests /
   users see same results).
5. **Add the new MCP tools**: `agent_run`, `agent_session_*`, `list_skills`.
   Local mode + remote mode both supported. Live smoke test from inside
   Claude Code.
6. **Update README + CLAUDE.md** with usage patterns: when to use `agent_run`
   vs `ask` / `scaffold`, mode-selection guidance, the local-bash opt-in env
   var, the skills naming convention.
7. **Optional follow-up:** a `superpowers`-side prompt-snippet that teaches
   Claude when to dispatch to vllm vs do work itself. Lives in `CLAUDE.md`
   so it overrides default behavior.

### Out of scope (YAGNI)

- Auth/multi-tenant on the VM HTTP API. Single user, single VM, ngrok-fronted.
  Add when there's a second user.
- Token cost tracking. The vLLM endpoint is local hardware.
- Distributed/parallel sessions. One worker per session, one session per
  goal. Multiple `agent_run` calls in flight is fine (each spawns a separate
  loop).
- A web UI for session inspection. Read the JSONL files.
- Skill compatibility shims / "worker variants" of skills. We pass the full
  SKILL.md verbatim; if a skill turns out to behave badly with Qwen, we can
  author a worker-variant later.

---

## Constraints & Risks

- **vLLM context window is 32k** (`--max-model-len 32768`). Skill content +
  worker tools schema + extra_context + working transcript must fit.
  System-prompt builder must enforce a budget, prefer truncating
  `extra_context` first, and warn when truncation occurs.
- **Qwen3-Coder-30B-A3B-Instruct-AWQ instruction-following will be lower
  fidelity than Opus.** Long skills (e.g. `brainstorming`) may not be
  reliably followed. If a skill misbehaves in practice, we author a
  worker-variant rather than fight the model.
- **Local mode with unrestricted Bash is dangerous by design.** Mitigated by
  the `VLLM_AGENT_LOCAL_BASH=1` opt-in. Documented as user-accepted risk.
- **Remote mode depends on git push/fetch to the VM.** Need a `vm-remote`
  git remote to be configured. Setup script handles this; if absent, the
  shim returns a clear error.
