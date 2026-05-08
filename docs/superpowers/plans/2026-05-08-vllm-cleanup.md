# Plan B Cleanup — Implementation Plan (Plan C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the 4 non-blocking follow-ups from the Plan B final code review — keep the codebase clean before any new features land on top.

**Architecture:** Four small targeted edits, each with its own commit. No new tests are added (the changes are docstring/cosmetic/infra), but the existing 71-test suite must still pass after every commit.

**Tech Stack:** No new dependencies. Edits to `mcp-server/vllm_mcp.py`, `profiles/rtx-inference.yaml.tpl`, `launch-inference.sh`.

**Source review:** Plan B final review by code-reviewer subagent — issues #5, #6, #4 (Important), #7 (Important).

---

## File Structure

```
rtx_5090_dev/
├── mcp-server/
│   └── vllm_mcp.py                    # MODIFY: docstring + asdict hoist
├── profiles/
│   └── rtx-inference.yaml.tpl         # MODIFY: + StartLimit*, + DDG_MIN_INTERVAL_S env
└── launch-inference.sh                 # MODIFY: pass DDG_INTERVAL into VM-side env (sed-substitute)
```

No new files; no new tests.

---

## Task 1: Update `vllm_mcp.py` module docstring to list 13 tools

**Issue (Plan B review #5):** Module docstring at the top of `mcp-server/vllm_mcp.py` still says "Tool surface (7 total)" and only lists the original 7 tools. After Plan B there are 13.

**Files:** Modify `mcp-server/vllm_mcp.py`.

- [ ] **Step 1: Read the current docstring**

```bash
sed -n '1,40p' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server/vllm_mcp.py
```

Locate the module docstring block at the top (lines 1–40 or so) — the triple-quoted string that starts `"""MCP stdio server wrapping a vLLM OpenAI-compatible endpoint.`

It currently has a section like:
```
Tool surface (7 total):

  Utility:
    - health()              probe the vLLM endpoint
    - list_models()         list served models
    - verify_project(path)  smoke-test a Python project on disk

  Generation (all write to disk, all have web search):
    - ask(prompt, out_path)            single-turn Q&A, writes the answer
    - converse(messages, out_path)     multi-turn dialog, writes the final reply
    - scaffold(prompt, out_dir)        multi-file project generation
    - critique(prompt, draft, out_path) draft → corrected version
```

- [ ] **Step 2: Replace the "Tool surface" block**

Replace the section above (find the exact text to know where to start/end) with:

```
Tool surface (13 total):

  Utility:
    - health()              probe the vLLM endpoint
    - list_models()         list served models
    - verify_project(path)  smoke-test a Python project on disk
    - list_skills()         list discoverable agent skills (project/user/superpowers)

  Generation (delegate to vllm_agent.loop with web_search-only palette;
   write to disk, return only metadata):
    - ask(prompt, out_path)             single-turn Q&A, writes the answer
    - converse(messages, out_path)      multi-turn dialog, writes the final reply
    - scaffold(prompt, out_dir)         multi-file project generation
    - critique(prompt, draft, out_path) draft → corrected version

  Agent dispatch (delegate to vllm_agent.api; mode='local' runs in-process,
   mode='remote' POSTs to VLLM_AGENT_URL inside the VM):
    - agent_run(task, ...)              one-shot coding-agent task
    - agent_session_start(goal, ...)    start a long-running session
    - agent_session_step(session_id)    run one step
    - agent_session_status(session_id)  read session state
    - agent_session_stop(session_id)    stop a session
```

- [ ] **Step 3: Verify the file still imports**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python3 -c "import vllm_mcp; print('imports OK')"
```
Expected: `imports OK`. If not, the docstring block was malformed (e.g., a triple-quote got broken). Fix.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan C: update vllm_mcp module docstring to list all 13 tools"
```

---

## Task 2: Hoist `from dataclasses import asdict` to module-level imports

**Issue (Plan B review #6):** Five MCP tool functions (`agent_run`, `agent_session_start`, `agent_session_step`, `agent_session_status`, `agent_session_stop`) each have an inline `from dataclasses import asdict` inside their `mode == "local"` branch. Hoist to a single import at the top of the module.

**Files:** Modify `mcp-server/vllm_mcp.py`.

- [ ] **Step 1: Find the existing import block**

```bash
grep -n 'from dataclasses\|^import\|^from' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server/vllm_mcp.py | head -30
```

Locate the existing `from dataclasses import asdict` statements (5 of them, all inline inside function bodies).

Locate the existing module-level imports block (top of file).

- [ ] **Step 2: Add the hoisted import**

In the module-level import block at the top of `vllm_mcp.py`, add (with the other `from X import Y` lines, e.g. near `from typing import Any` if present, or before the `from mcp.server.fastmcp import FastMCP` line):

```python
from dataclasses import asdict
```

If a top-level `from dataclasses import` line already exists for some other dataclass utility, just append `, asdict` to it.

- [ ] **Step 3: Remove the 5 inline imports**

Search for and remove each line `        from dataclasses import asdict` inside the 5 tool function bodies. (Pay attention to indentation — they're inside `if mode == "local":` blocks, so likely indented 8 spaces.)

```bash
grep -n 'from dataclasses import asdict' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server/vllm_mcp.py
```

Before the edit: 5 matches (4 inline + maybe 1 top-level if existed before).
After the edit: 1 match (the new top-level import).

- [ ] **Step 4: Verify imports + smoke**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python3 -c "
import vllm_mcp
import inspect
funcs = [n for n,o in inspect.getmembers(vllm_mcp) if inspect.iscoroutinefunction(o) and not n.startswith('_')]
print(f'tools: {len(funcs)}')
assert 'agent_run' in funcs and 'list_skills' in funcs
print('OK')
"
```
Expected: `tools: 13` and `OK`.

- [ ] **Step 5: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add mcp-server/vllm_mcp.py
git commit -m "Plan C: hoist dataclasses.asdict import to module top"
```

---

## Task 3: Harden systemd units with `StartLimitBurst` / `StartLimitIntervalSec`

**Issue (Plan B review #4):** Both `vllm.service` and `vllm-agent.service` have `Restart=on-failure` with no rate limit. A persistently broken unit will respawn forever and spam the journal. Add `StartLimitIntervalSec=120` and `StartLimitBurst=5` to both `[Unit]` sections so a broken unit gives up cleanly (status `failed`).

**Files:** Modify `profiles/rtx-inference.yaml.tpl`.

- [ ] **Step 1: Read the current systemd-unit blocks**

```bash
grep -nB1 -A20 'StartLimitBurst\|RestartSec\|^\[Unit\]\|Description=vllm' /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/profiles/rtx-inference.yaml.tpl
```

The file has TWO inlined systemd unit definitions in heredocs:
- `vllm.service` (the existing one for vLLM)
- `vllm-agent.service` (added in Plan B)

- [ ] **Step 2: Add `StartLimitIntervalSec` and `StartLimitBurst` to both `[Unit]` sections**

For EACH unit, find the `[Unit]` section and add these two lines AT THE END of that section (just before the blank line that precedes `[Service]`):

```
StartLimitIntervalSec=120
StartLimitBurst=5
```

Concretely, the `vllm-agent.service` `[Unit]` block currently looks like:
```
    [Unit]
    Description=vllm-agent HTTP server (agent runtime backed by local vLLM)
    After=network-online.target vllm.service
    Wants=network-online.target
```

After:
```
    [Unit]
    Description=vllm-agent HTTP server (agent runtime backed by local vLLM)
    After=network-online.target vllm.service
    Wants=network-online.target
    StartLimitIntervalSec=120
    StartLimitBurst=5
```

Apply the same two-line addition to the `vllm.service` `[Unit]` section.

- [ ] **Step 3: Validate the YAML still parses**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev && python3 -c "
import yaml
with open('profiles/rtx-inference.yaml.tpl') as f:
    text = f.read()
text = (text
        .replace('__VLLM_MODEL__', 'p').replace('__PROFILE_NAME__', 'p')
        .replace('__VLLM_MAX_LEN__', '32768').replace('__VLLM_GPU_UTIL__', '0.92')
        .replace('__VLLM_QUANT__', 'awq').replace('__VLLM_API_KEY_ARG__', '')
        .replace('__BRIDGE__', 'br').replace('__STORAGE_POOL__', 'pool')
        .replace('__ROOT_SIZE__', '100GiB').replace('__LIMITS_CPU__', '4')
        .replace('__LIMITS_MEMORY__', '32GiB'))
yaml.safe_load(text)
print('yaml OK')
"
```
Expected: `yaml OK`.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add profiles/rtx-inference.yaml.tpl
git commit -m "Plan C: harden vllm + vllm-agent systemd units with StartLimitBurst"
```

---

## Task 4: Thread `DDG_MIN_INTERVAL_S` into VM-side `vllm-agent.service` environment

**Issue (Plan B review #7):** The `--ddg-interval` CLI flag in `launch-inference.sh` only writes `DDG_MIN_INTERVAL_S` into the LOCAL `.mcp.json`. The VM-side `vllm-agent.service` doesn't receive it, so remote-mode `web_search` always uses the default 1.5s. Thread the value through.

**Files:** Modify `profiles/rtx-inference.yaml.tpl`, `launch-inference.sh`.

- [ ] **Step 1: Add a new placeholder to the cloud-init template**

In `profiles/rtx-inference.yaml.tpl`, find the `vllm-agent.service` heredoc, locate the `[Service]` section. After the existing `Environment=VLLM_MODEL=__VLLM_MODEL__` line, add:

```
    Environment=DDG_MIN_INTERVAL_S=__DDG_MIN_INTERVAL__
```

(Match the indentation of surrounding `Environment=` lines exactly.)

- [ ] **Step 2: Substitute the placeholder in `launch-inference.sh`**

In `launch-inference.sh`, find the existing `sed` pipeline that renders the template (it has lines like `-e "s|__VLLM_MODEL__|$MODEL|g"` etc). Add a new substitution for the new placeholder:

```bash
  -e "s|__DDG_MIN_INTERVAL__|$DDG_INTERVAL|g" \
```

(Place it next to the other `-e "s|...|...|g" \` lines, preserving the trailing backslash. The variable `$DDG_INTERVAL` is already defined at the top of the script with default `1.5`.)

- [ ] **Step 3: Validate**

YAML parse check:
```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev && python3 -c "
import yaml
with open('profiles/rtx-inference.yaml.tpl') as f:
    text = f.read()
text = (text
        .replace('__VLLM_MODEL__', 'p').replace('__PROFILE_NAME__', 'p')
        .replace('__VLLM_MAX_LEN__', '32768').replace('__VLLM_GPU_UTIL__', '0.92')
        .replace('__VLLM_QUANT__', 'awq').replace('__VLLM_API_KEY_ARG__', '')
        .replace('__BRIDGE__', 'br').replace('__STORAGE_POOL__', 'pool')
        .replace('__ROOT_SIZE__', '100GiB').replace('__LIMITS_CPU__', '4')
        .replace('__LIMITS_MEMORY__', '32GiB').replace('__DDG_MIN_INTERVAL__', '1.5'))
yaml.safe_load(text)
print('yaml OK')
"
```

Bash syntax check:
```bash
bash -n /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/launch-inference.sh && echo "bash OK"
```

Both must succeed.

- [ ] **Step 4: Commit**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git add profiles/rtx-inference.yaml.tpl launch-inference.sh
git commit -m "Plan C: thread DDG_MIN_INTERVAL_S into VM-side vllm-agent env"
```

---

## Final verification

- [ ] **Step 1: All 4 commits present**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev
git log --oneline a86fac5..HEAD
```
Expected: 4 commits with the messages above.

- [ ] **Step 2: vllm-agent test suite still passes**

```bash
cd vllm-agent && uv run pytest -v
```
Expected: 71 PASS, 1 deselected (no regressions).

- [ ] **Step 3: vllm_mcp imports**

```bash
cd /home/bdx/allcode/github/vantagecompute/rtx_5090_dev/mcp-server && python3 -c "
import vllm_mcp
import inspect
tools = sorted(n for n,o in inspect.getmembers(vllm_mcp) if inspect.iscoroutinefunction(o) and not n.startswith('_'))
print(f'{len(tools)} tools: {tools}')
assert len(tools) == 13
"
```
Expected: 13 tools listed.

- [ ] **Step 4: bash + yaml validate**

Already done in each task; run once more end-to-end if you want.

---

## Self-Review

### Spec coverage
| Issue | Task |
|---|---|
| Plan B review #5 (docstring "7 total") | Task 1 |
| Plan B review #6 (inline asdict imports) | Task 2 |
| Plan B review #4 (StartLimitBurst) | Task 3 |
| Plan B review #7 (DDG_MIN_INTERVAL_S) | Task 4 |

### Placeholder scan
None. Every step has full code or exact commands.

### Type consistency
The plan does not introduce new types. Names referenced (`agent_run`, `list_skills`, `vllm-agent.service`, `DDG_MIN_INTERVAL_S`, etc.) are consistent with the existing code from Plans A and B.

No issues found.
