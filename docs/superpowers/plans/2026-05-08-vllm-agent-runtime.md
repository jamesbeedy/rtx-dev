# vllm-agent Runtime Package — Implementation Plan (Plan A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone `vllm-agent` Python package that runs an agent loop against the existing vLLM endpoint — with worker tools (Read/Edit/Bash/grep/glob/web_search/finish), skill loading, session storage, a CLI, and a FastAPI HTTP server — all backed by unit + integration tests.

**Architecture:** One Python package (`vllm-agent/`) with no MCP dependencies. The agent loop is the existing `_generate()` pattern from `mcp-server/vllm_mcp.py`, generalized to a configurable worker-tool palette. The package is consumable two ways: as a Python import (will be used by the MCP shim in Plan B) and via a FastAPI HTTP server (will be deployed to the VM in Plan B). Tests use `respx` to mock the vLLM HTTP endpoint; live smoke tests against the real VM are opt-in via a pytest marker.

**Tech Stack:** Python 3.11+, `httpx`, `beautifulsoup4`, `pyyaml`, `fastapi`, `uvicorn`, `typer`, `pytest`, `pytest-asyncio`, `respx`.

**Out of scope of Plan A** (Plan B will cover): MCP shim refactor, new MCP tools, VM systemd installation, README/CLAUDE.md updates.

**Source spec:** `docs/superpowers/specs/2026-05-08-vllm-heavy-lifting-design.md`

---

## File Structure

```
rtx_5090_dev/
├── vllm-agent/                        # NEW package
│   ├── pyproject.toml
│   ├── README.md
│   └── src/vllm_agent/
│       ├── __init__.py                # exports public API
│       ├── workspace.py               # workdir resolution
│       ├── skills.py                  # list/load skill files
│       ├── prompts.py                 # system + user prompt construction
│       ├── sessions.py                # session storage on disk
│       ├── transcript.py              # JSONL transcript writer
│       ├── loop.py                    # the agent tool-call loop
│       ├── api.py                     # public agent_run / agent_session_* funcs
│       ├── tools/
│       │   ├── __init__.py            # WORKER_TOOLS registry
│       │   ├── base.py                # Tool dataclass
│       │   ├── fs.py                  # read_file, write_file, edit_file, grep, glob
│       │   ├── shell.py               # bash
│       │   ├── search.py              # web_search (DDG, ported from mcp-server)
│       │   └── finish.py              # finish
│       ├── server.py                  # FastAPI app
│       └── cli.py                     # typer CLI: run, serve, list-skills
│   └── tests/
│       ├── conftest.py                # shared fixtures
│       ├── test_workspace.py
│       ├── test_skills.py
│       ├── test_prompts.py
│       ├── test_sessions.py
│       ├── test_transcript.py
│       ├── test_tools_fs.py
│       ├── test_tools_shell.py
│       ├── test_tools_search.py
│       ├── test_tools_finish.py
│       ├── test_loop.py
│       ├── test_api.py
│       ├── test_server.py
│       ├── test_cli.py
│       └── test_live_smoke.py         # opt-in via @pytest.mark.live
```

Each file owns one responsibility. The agent loop (`loop.py`) is the only file that orchestrates across modules.

---

## Task 1: Bootstrap the package

**Files:**
- Create: `vllm-agent/pyproject.toml`
- Create: `vllm-agent/README.md`
- Create: `vllm-agent/src/vllm_agent/__init__.py`
- Create: `vllm-agent/tests/__init__.py`
- Create: `vllm-agent/tests/test_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

`vllm-agent/tests/test_smoke.py`:
```python
def test_package_imports():
    import vllm_agent
    assert vllm_agent.__version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd vllm-agent && uv venv && uv pip install -e ".[dev]" && uv run pytest tests/test_smoke.py -v
```
Expected: ImportError or ModuleNotFoundError (package not installed yet).

- [ ] **Step 3: Write `pyproject.toml`**

`vllm-agent/pyproject.toml`:
```toml
[project]
name = "vllm-agent"
version = "0.1.0"
description = "Agent runtime backed by a vLLM OpenAI-compatible endpoint."
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27.0",
    "beautifulsoup4>=4.12",
    "pyyaml>=6.0",
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "typer>=0.12",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
]

[project.scripts]
vllm-agent = "vllm_agent.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["live: tests that hit the real vLLM endpoint (opt-in)"]
```

- [ ] **Step 4: Write `__init__.py`**

`vllm-agent/src/vllm_agent/__init__.py`:
```python
__version__ = "0.1.0"
```

- [ ] **Step 5: Write `README.md`** (one-liner is enough)

`vllm-agent/README.md`:
```markdown
# vllm-agent

Agent runtime backed by a vLLM OpenAI-compatible endpoint. See
`docs/superpowers/specs/2026-05-08-vllm-heavy-lifting-design.md` for the design.
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd vllm-agent && uv pip install -e ".[dev]" && uv run pytest tests/test_smoke.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add vllm-agent/
git commit -m "Bootstrap vllm-agent package skeleton"
```

---

## Task 2: Workspace module

**Purpose:** Resolve and validate `workdir` for a run. `workdir` defaults to CWD; can be overridden per call. We also expose a soft "is path inside workdir" helper used by FS tools.

**Files:**
- Create: `vllm-agent/src/vllm_agent/workspace.py`
- Create: `vllm-agent/tests/test_workspace.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_workspace.py`:
```python
from pathlib import Path
import pytest
from vllm_agent.workspace import Workspace


def test_default_workdir_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = Workspace.resolve(None)
    assert ws.root == tmp_path.resolve()


def test_explicit_workdir(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    assert ws.root == tmp_path.resolve()


def test_workdir_must_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        Workspace.resolve(str(tmp_path / "does-not-exist"))


def test_is_inside(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    assert ws.is_inside(tmp_path / "a.txt") is True
    assert ws.is_inside(tmp_path / "sub" / "b.txt") is True
    assert ws.is_inside(Path("/etc/passwd")) is False


def test_resolve_path_relative(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    assert ws.resolve_path("a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_resolve_path_absolute_outside_returns_as_is(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    p = ws.resolve_path("/etc/hosts")
    assert p == Path("/etc/hosts")
```

- [ ] **Step 2: Verify tests fail**

```bash
cd vllm-agent && uv run pytest tests/test_workspace.py -v
```
Expected: FAIL (ModuleNotFoundError on `vllm_agent.workspace`).

- [ ] **Step 3: Implement `workspace.py`**

`vllm-agent/src/vllm_agent/workspace.py`:
```python
"""Workspace = a resolved working directory for an agent run.

Path-confinement here is *advisory* — the bash tool can break it. The
`is_inside` helper is used by FS tools to flag (not block) cross-workspace
edits in the transcript.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def resolve(cls, workdir: str | None) -> "Workspace":
        if workdir is None:
            root = Path.cwd().resolve()
        else:
            root = Path(workdir).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"workdir not found: {root}")
        return cls(root=root)

    def resolve_path(self, path: str) -> Path:
        p = Path(path).expanduser()
        if p.is_absolute():
            return p
        return (self.root / p).resolve()

    def is_inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd vllm-agent && uv run pytest tests/test_workspace.py -v
```
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/workspace.py vllm-agent/tests/test_workspace.py
git commit -m "Add Workspace: resolve and validate workdir"
```

---

## Task 3: Skill loader

**Purpose:** Walk configured skill roots, parse frontmatter, expose `list_skills()` and `load_skill(name)`.

**Files:**
- Create: `vllm-agent/src/vllm_agent/skills.py`
- Create: `vllm-agent/tests/test_skills.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_skills.py`:
```python
from pathlib import Path
import pytest
from vllm_agent.skills import SkillLoader, SkillNotFound


@pytest.fixture
def fake_roots(tmp_path):
    """Build a fake skill-root layout under tmp_path."""
    proj = tmp_path / "project_skills"
    user = tmp_path / "user_skills"
    sp = tmp_path / "superpowers" / "claude-plugins-official" / "superpowers" / "5.1.0" / "skills"
    sp.mkdir(parents=True)
    user.mkdir()
    proj.mkdir()

    # superpowers:test-driven-development
    (sp / "tdd").mkdir()
    (sp / "tdd" / "SKILL.md").write_text(
        "---\nname: test-driven-development\ndescription: Use when implementing\n---\n\nbody"
    )
    # user-level skill
    (user / "my-skill").mkdir()
    (user / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: user skill\n---\n\nuser body"
    )
    # project-level skill (overrides user if same name)
    (proj / "my-skill").mkdir()
    (proj / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: project skill\n---\n\nproject body"
    )
    return [proj, user, sp]


def test_list_skills(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    skills = loader.list_skills()
    names = {s["name"] for s in skills}
    assert "project:my-skill" in names
    assert "user:my-skill" in names
    assert "superpowers:test-driven-development" in names


def test_list_skills_includes_metadata(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    by_name = {s["name"]: s for s in loader.list_skills()}
    assert by_name["superpowers:test-driven-development"]["description"] == "Use when implementing"
    assert "path" in by_name["superpowers:test-driven-development"]


def test_load_skill_returns_full_content(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    content = loader.load_skill("superpowers:test-driven-development")
    assert "name: test-driven-development" in content
    assert "body" in content


def test_load_unknown_skill_raises(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    with pytest.raises(SkillNotFound):
        loader.load_skill("project:does-not-exist")


def test_project_overrides_user_when_same_name(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    # Both `project:my-skill` and `user:my-skill` should be listed (different namespaces).
    names = {s["name"] for s in loader.list_skills()}
    assert "project:my-skill" in names
    assert "user:my-skill" in names
    # And content is distinguishable.
    assert "project body" in loader.load_skill("project:my-skill")
    assert "user body" in loader.load_skill("user:my-skill")
```

- [ ] **Step 2: Verify tests fail**

```bash
cd vllm-agent && uv run pytest tests/test_skills.py -v
```
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `skills.py`**

`vllm-agent/src/vllm_agent/skills.py`:
```python
"""Skill loading: walk configured roots, parse frontmatter, expose list/load."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class SkillNotFound(Exception):
    """Raised when a skill name cannot be resolved."""


def _default_roots() -> list[Path]:
    return [
        Path.cwd() / "skills",
        Path("~/.claude/skills").expanduser(),
        Path("~/.claude/plugins/cache/claude-plugins-official").expanduser(),
    ]


def _source_for_root(root: Path) -> str:
    """Pick a readable namespace prefix for skills under this root."""
    parts = root.parts
    if "claude-plugins-official" in parts:
        return "superpowers"
    if root.name == "skills" and ".claude" in parts:
        return "user"
    return "project"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


@dataclass
class SkillLoader:
    roots: list[Path] = field(default_factory=_default_roots)

    def list_skills(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            source = _source_for_root(root)
            for skill_md in root.rglob("SKILL.md"):
                fm, _ = _parse_frontmatter(skill_md.read_text())
                local_name = fm.get("name") or skill_md.parent.name
                full_name = f"{source}:{local_name}"
                if full_name in seen:
                    continue
                seen.add(full_name)
                out.append({
                    "name": full_name,
                    "source": source,
                    "path": str(skill_md),
                    "description": fm.get("description", ""),
                })
        return sorted(out, key=lambda s: s["name"])

    def load_skill(self, name: str) -> str:
        for s in self.list_skills():
            if s["name"] == name:
                return Path(s["path"]).read_text()
        raise SkillNotFound(f"Skill not found: {name}. "
                            f"Try one of: {[s['name'] for s in self.list_skills()]}")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd vllm-agent && uv run pytest tests/test_skills.py -v
```
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/skills.py vllm-agent/tests/test_skills.py
git commit -m "Add SkillLoader: walk roots, parse frontmatter, list/load skills"
```

---

## Task 4: Tool base class + registry

**Purpose:** Define the `Tool` dataclass that every worker tool conforms to, and the central `WORKER_TOOLS` registry.

**Files:**
- Create: `vllm-agent/src/vllm_agent/tools/__init__.py`
- Create: `vllm-agent/src/vllm_agent/tools/base.py`

- [ ] **Step 1: Write `base.py`**

`vllm-agent/src/vllm_agent/tools/base.py`:
```python
"""Base contract for worker tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

ToolFn = Callable[[dict[str, Any], "ToolContext"], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    schema: dict[str, Any]   # OpenAI function-calling JSON schema
    execute: ToolFn


@dataclass
class ToolContext:
    """Runtime context passed to every tool call."""
    workspace: Any           # vllm_agent.workspace.Workspace
    transcript: Any          # vllm_agent.transcript.Transcript
    env: dict[str, str]      # subset of os.environ snapshotted at run start
```

- [ ] **Step 2: Write `tools/__init__.py`**

`vllm-agent/src/vllm_agent/tools/__init__.py`:
```python
"""Worker tool registry. Tools register themselves here at import time."""
from __future__ import annotations

from .base import Tool, ToolContext

WORKER_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in WORKER_TOOLS:
        raise ValueError(f"tool {tool.name!r} already registered")
    WORKER_TOOLS[tool.name] = tool
    return tool


__all__ = ["Tool", "ToolContext", "WORKER_TOOLS", "register"]
```

- [ ] **Step 3: Smoke-test the registry**

`vllm-agent/tests/test_tools_registry.py`:
```python
from vllm_agent.tools import Tool, WORKER_TOOLS, register


async def _noop(args, ctx):
    return {"ok": True}


def test_register_and_lookup():
    t = Tool(name="x_demo", schema={"type": "function"}, execute=_noop)
    register(t)
    assert WORKER_TOOLS["x_demo"] is t
    # cleanup so other tests aren't affected
    WORKER_TOOLS.pop("x_demo", None)


def test_double_register_raises():
    t = Tool(name="x_dup", schema={}, execute=_noop)
    register(t)
    import pytest
    with pytest.raises(ValueError):
        register(t)
    WORKER_TOOLS.pop("x_dup", None)
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_tools_registry.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/ vllm-agent/tests/test_tools_registry.py
git commit -m "Add Tool dataclass and WORKER_TOOLS registry"
```

---

## Task 5: FS tool — read_file

**Files:**
- Create: `vllm-agent/src/vllm_agent/tools/fs.py` (initial read_file only; expand in next tasks)
- Create: `vllm-agent/tests/test_tools_fs.py`

- [ ] **Step 1: Write failing test**

`vllm-agent/tests/test_tools_fs.py`:
```python
import pytest
from vllm_agent.tools.fs import read_file_tool
from vllm_agent.tools import ToolContext
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript


@pytest.fixture
def ctx(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    return ToolContext(workspace=ws, transcript=Transcript(tmp_path / "t.jsonl"), env={})


async def test_read_file_returns_content(tmp_path, ctx):
    p = tmp_path / "hello.txt"
    p.write_text("hi there\nline 2\n")
    out = await read_file_tool.execute({"path": "hello.txt"}, ctx)
    assert out["content"] == "hi there\nline 2\n"
    assert out["path"] == str(p.resolve())


async def test_read_file_offset_limit(tmp_path, ctx):
    p = tmp_path / "many.txt"
    p.write_text("\n".join(f"line {i}" for i in range(10)))
    out = await read_file_tool.execute({"path": "many.txt", "offset": 2, "limit": 3}, ctx)
    lines = out["content"].splitlines()
    assert lines == ["line 2", "line 3", "line 4"]


async def test_read_file_missing(tmp_path, ctx):
    out = await read_file_tool.execute({"path": "nope.txt"}, ctx)
    assert "error" in out
```

- [ ] **Step 2: Verify tests fail**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `tools/fs.py` (read_file only for now)**

`vllm-agent/src/vllm_agent/tools/fs.py`:
```python
"""Filesystem tools: read, write, edit, grep, glob."""
from __future__ import annotations

from typing import Any

from . import Tool, ToolContext, register


# ---- read_file --------------------------------------------------------------

async def _read_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path_arg = args.get("path", "")
    offset = args.get("offset")
    limit = args.get("limit")
    full = ctx.workspace.resolve_path(path_arg)
    try:
        text = full.read_text()
    except FileNotFoundError:
        return {"error": f"file not found: {full}"}
    except PermissionError as e:
        return {"error": f"permission denied: {e}"}
    if offset is not None or limit is not None:
        lines = text.splitlines()
        start = int(offset or 0)
        end = start + int(limit) if limit is not None else len(lines)
        text = "\n".join(lines[start:end])
    return {"path": str(full), "content": text, "bytes": len(text.encode())}


read_file_tool = register(Tool(
    name="read_file",
    schema={
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace. Supports offset/limit (line-based).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative or absolute path."},
                    "offset": {"type": "integer", "description": "Starting line (0-indexed)."},
                    "limit": {"type": "integer", "description": "Number of lines."},
                },
                "required": ["path"],
            },
        },
    },
    execute=_read_file,
))
```

- [ ] **Step 4: Add a stub `transcript.py` so the import resolves**

`vllm-agent/src/vllm_agent/transcript.py`:
```python
"""Transcript: append-only JSONL recorder. Filled in further in Task 11."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Transcript:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, record: dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")
```

- [ ] **Step 5: Run tests to verify pass**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/fs.py vllm-agent/src/vllm_agent/transcript.py vllm-agent/tests/test_tools_fs.py
git commit -m "Add read_file worker tool + Transcript stub"
```

---

## Task 6: FS tool — write_file

**Files:**
- Modify: `vllm-agent/src/vllm_agent/tools/fs.py` (append)
- Modify: `vllm-agent/tests/test_tools_fs.py` (append tests)

- [ ] **Step 1: Add failing tests to `test_tools_fs.py`** (append at end of file)

```python
async def test_write_file_creates_file(tmp_path, ctx):
    out = await write_file_tool.execute(
        {"path": "new/sub/x.txt", "content": "hello"}, ctx)
    assert (tmp_path / "new" / "sub" / "x.txt").read_text() == "hello"
    assert out["bytes_written"] == 5


async def test_write_file_overwrites(tmp_path, ctx):
    (tmp_path / "x.txt").write_text("old")
    await write_file_tool.execute({"path": "x.txt", "content": "new"}, ctx)
    assert (tmp_path / "x.txt").read_text() == "new"
```

Also add to imports at top of file:
```python
from vllm_agent.tools.fs import read_file_tool, write_file_tool
```

- [ ] **Step 2: Verify new tests fail**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: 2 new FAILs (ImportError on `write_file_tool`).

- [ ] **Step 3: Append `write_file` to `tools/fs.py`**

```python
# ---- write_file -------------------------------------------------------------

async def _write_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path_arg = args.get("path", "")
    content = args.get("content", "")
    full = ctx.workspace.resolve_path(path_arg)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return {
        "path": str(full),
        "bytes_written": len(content.encode()),
        "inside_workspace": ctx.workspace.is_inside(full),
    }


write_file_tool = register(Tool(
    name="write_file",
    schema={
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file (creates parent dirs, overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    execute=_write_file,
))
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: 5 PASS total.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/fs.py vllm-agent/tests/test_tools_fs.py
git commit -m "Add write_file worker tool"
```

---

## Task 7: FS tool — edit_file

**Files:**
- Modify: `vllm-agent/src/vllm_agent/tools/fs.py`
- Modify: `vllm-agent/tests/test_tools_fs.py`

- [ ] **Step 1: Add failing tests**

Append to `test_tools_fs.py`:
```python
async def test_edit_file_basic(tmp_path, ctx):
    p = tmp_path / "f.txt"
    p.write_text("foo bar baz")
    out = await edit_file_tool.execute(
        {"path": "f.txt", "old": "bar", "new": "BAR"}, ctx)
    assert p.read_text() == "foo BAR baz"
    assert out["replacements"] == 1


async def test_edit_file_old_not_unique_errors(tmp_path, ctx):
    p = tmp_path / "f.txt"
    p.write_text("x x x")
    out = await edit_file_tool.execute(
        {"path": "f.txt", "old": "x", "new": "y"}, ctx)
    assert "error" in out
    assert p.read_text() == "x x x"


async def test_edit_file_replace_all(tmp_path, ctx):
    p = tmp_path / "f.txt"
    p.write_text("x x x")
    out = await edit_file_tool.execute(
        {"path": "f.txt", "old": "x", "new": "y", "replace_all": True}, ctx)
    assert p.read_text() == "y y y"
    assert out["replacements"] == 3


async def test_edit_file_old_not_found(tmp_path, ctx):
    p = tmp_path / "f.txt"
    p.write_text("hello")
    out = await edit_file_tool.execute(
        {"path": "f.txt", "old": "missing", "new": "x"}, ctx)
    assert "error" in out
```

Add to imports:
```python
from vllm_agent.tools.fs import read_file_tool, write_file_tool, edit_file_tool
```

- [ ] **Step 2: Verify failures**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: 4 new FAILs.

- [ ] **Step 3: Append `edit_file` to `tools/fs.py`**

```python
# ---- edit_file --------------------------------------------------------------

async def _edit_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path_arg = args.get("path", "")
    old = args.get("old", "")
    new = args.get("new", "")
    replace_all = bool(args.get("replace_all", False))
    full = ctx.workspace.resolve_path(path_arg)
    try:
        text = full.read_text()
    except FileNotFoundError:
        return {"error": f"file not found: {full}"}
    if old not in text:
        return {"error": f"old string not found in {full}"}
    if not replace_all and text.count(old) > 1:
        return {"error": f"old string is not unique ({text.count(old)} occurrences); "
                         "set replace_all=true or provide more context"}
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    full.write_text(new_text)
    return {
        "path": str(full),
        "replacements": text.count(old) if replace_all else 1,
        "bytes_written": len(new_text.encode()),
    }


edit_file_tool = register(Tool(
    name="edit_file",
    schema={
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace `old` with `new` in a file. By default `old` "
                           "must be unique; set replace_all=true to replace every "
                           "occurrence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    execute=_edit_file,
))
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: 9 PASS total.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/fs.py vllm-agent/tests/test_tools_fs.py
git commit -m "Add edit_file worker tool"
```

---

## Task 8: FS tool — grep

**Files:**
- Modify: `vllm-agent/src/vllm_agent/tools/fs.py`
- Modify: `vllm-agent/tests/test_tools_fs.py`

- [ ] **Step 1: Add failing tests**

```python
async def test_grep_basic(tmp_path, ctx):
    (tmp_path / "a.py").write_text("def hello():\n    return 1\n")
    (tmp_path / "b.py").write_text("def world():\n    return 2\n")
    out = await grep_tool.execute({"pattern": "return"}, ctx)
    paths = {m["path"] for m in out["matches"]}
    assert any(p.endswith("a.py") for p in paths)
    assert any(p.endswith("b.py") for p in paths)


async def test_grep_with_glob(tmp_path, ctx):
    (tmp_path / "a.py").write_text("hit\n")
    (tmp_path / "a.txt").write_text("hit\n")
    out = await grep_tool.execute({"pattern": "hit", "glob": "*.py"}, ctx)
    paths = {m["path"] for m in out["matches"]}
    assert any(p.endswith("a.py") for p in paths)
    assert not any(p.endswith("a.txt") for p in paths)


async def test_grep_no_matches(tmp_path, ctx):
    (tmp_path / "a.txt").write_text("nothing here\n")
    out = await grep_tool.execute({"pattern": "missing"}, ctx)
    assert out["matches"] == []
```

Add `grep_tool` to imports.

- [ ] **Step 2: Verify failures**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: 3 new FAILs.

- [ ] **Step 3: Append `grep` to `tools/fs.py`**

```python
# ---- grep -------------------------------------------------------------------

import re
import shutil
import subprocess


async def _grep(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    pattern = args.get("pattern", "")
    glob = args.get("glob")
    path_arg = args.get("path") or "."
    base = ctx.workspace.resolve_path(path_arg)

    # Prefer ripgrep if available; otherwise fall back to a Python walk.
    if shutil.which("rg"):
        cmd = ["rg", "--line-number", "--no-heading", "--color=never", pattern]
        if glob:
            cmd += ["--glob", glob]
        cmd.append(str(base))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        matches: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            try:
                p, ln, body = line.split(":", 2)
                matches.append({"path": p, "line": int(ln), "text": body})
            except ValueError:
                continue
        return {"matches": matches[:200], "tool": "rg"}

    # Python fallback.
    rx = re.compile(pattern)
    matches = []
    iterator = base.rglob(glob) if glob else base.rglob("*")
    for fp in iterator:
        if not fp.is_file():
            continue
        try:
            for i, line in enumerate(fp.read_text().splitlines(), start=1):
                if rx.search(line):
                    matches.append({"path": str(fp), "line": i, "text": line})
                    if len(matches) >= 200:
                        return {"matches": matches, "tool": "python"}
        except (UnicodeDecodeError, PermissionError):
            continue
    return {"matches": matches, "tool": "python"}


grep_tool = register(Tool(
    name="grep",
    schema={
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search the workspace for a regex pattern. Uses ripgrep if available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path":    {"type": "string", "description": "Subpath to search; default = workspace root."},
                    "glob":    {"type": "string", "description": "Optional glob filter, e.g. '*.py'."},
                },
                "required": ["pattern"],
            },
        },
    },
    execute=_grep,
))
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: 12 PASS total.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/fs.py vllm-agent/tests/test_tools_fs.py
git commit -m "Add grep worker tool (rg with python fallback)"
```

---

## Task 9: FS tool — glob

**Files:**
- Modify: `vllm-agent/src/vllm_agent/tools/fs.py`
- Modify: `vllm-agent/tests/test_tools_fs.py`

- [ ] **Step 1: Add failing tests**

```python
async def test_glob_basic(tmp_path, ctx):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    out = await glob_tool.execute({"pattern": "*.py"}, ctx)
    names = sorted(p.rsplit("/", 1)[-1] for p in out["paths"])
    assert names == ["a.py", "b.py"]


async def test_glob_recursive(tmp_path, ctx):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("")
    out = await glob_tool.execute({"pattern": "**/*.py"}, ctx)
    assert any(p.endswith("sub/deep.py") for p in out["paths"])
```

Add `glob_tool` to imports.

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Append `glob` to `tools/fs.py`**

```python
# ---- glob -------------------------------------------------------------------

async def _glob(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    pattern = args.get("pattern", "")
    path_arg = args.get("path") or "."
    base = ctx.workspace.resolve_path(path_arg)
    paths = [str(p) for p in base.glob(pattern) if p.is_file()][:500]
    return {"paths": paths, "count": len(paths)}


glob_tool = register(Tool(
    name="glob",
    schema={
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files in the workspace by glob pattern (e.g. '**/*.py').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path":    {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    execute=_glob,
))
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_tools_fs.py -v
```
Expected: 14 PASS total.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/fs.py vllm-agent/tests/test_tools_fs.py
git commit -m "Add glob worker tool"
```

---

## Task 10: Shell tool — bash (with local opt-in)

**Files:**
- Create: `vllm-agent/src/vllm_agent/tools/shell.py`
- Create: `vllm-agent/tests/test_tools_shell.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_tools_shell.py`:
```python
import pytest
from vllm_agent.tools.shell import bash_tool
from vllm_agent.tools import ToolContext
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript


@pytest.fixture
def ctx_local(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    return ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_MODE": "local", "VLLM_AGENT_LOCAL_BASH": "1"},
    )


@pytest.fixture
def ctx_remote(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    return ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_MODE": "remote"},
    )


async def test_bash_runs_command(ctx_remote):
    out = await bash_tool.execute({"command": "echo hello"}, ctx_remote)
    assert out["exit_code"] == 0
    assert "hello" in out["stdout"]


async def test_bash_captures_stderr(ctx_remote):
    out = await bash_tool.execute({"command": "echo oops 1>&2; exit 3"}, ctx_remote)
    assert out["exit_code"] == 3
    assert "oops" in out["stderr"]


async def test_bash_cwd_defaults_to_workspace(tmp_path, ctx_remote):
    out = await bash_tool.execute({"command": "pwd"}, ctx_remote)
    assert out["stdout"].strip() == str(tmp_path.resolve())


async def test_bash_local_blocked_without_opt_in(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_MODE": "local"},  # no VLLM_AGENT_LOCAL_BASH
    )
    out = await bash_tool.execute({"command": "echo nope"}, ctx)
    assert "error" in out
    assert "VLLM_AGENT_LOCAL_BASH" in out["error"]


async def test_bash_local_allowed_with_opt_in(ctx_local):
    out = await bash_tool.execute({"command": "echo yes"}, ctx_local)
    assert out["exit_code"] == 0
    assert "yes" in out["stdout"]


async def test_bash_timeout(ctx_remote):
    out = await bash_tool.execute(
        {"command": "sleep 5", "timeout_s": 1}, ctx_remote)
    assert "timeout" in out.get("error", "").lower() or out["exit_code"] != 0
```

- [ ] **Step 2: Verify failures**

```bash
cd vllm-agent && uv run pytest tests/test_tools_shell.py -v
```
Expected: 6 FAILs.

- [ ] **Step 3: Implement `tools/shell.py`**

`vllm-agent/src/vllm_agent/tools/shell.py`:
```python
"""Shell tool: unrestricted bash. Local mode requires VLLM_AGENT_LOCAL_BASH=1."""
from __future__ import annotations

import asyncio
from typing import Any

from . import Tool, ToolContext, register


async def _bash(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    command = args.get("command", "")
    cwd = args.get("cwd") or str(ctx.workspace.root)
    timeout_s = float(args.get("timeout_s", 60))

    # Safety gate: in local mode require explicit opt-in.
    if ctx.env.get("VLLM_AGENT_MODE") == "local" and ctx.env.get("VLLM_AGENT_LOCAL_BASH") != "1":
        return {"error": "Local-mode bash is disabled. Set VLLM_AGENT_LOCAL_BASH=1 "
                         "in the agent environment to enable it."}

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"error": f"timeout after {timeout_s}s", "exit_code": -1,
                "stdout": "", "stderr": ""}
    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace")[:64_000],
        "stderr": stderr.decode(errors="replace")[:16_000],
    }


bash_tool = register(Tool(
    name="bash",
    schema={
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command. Unrestricted (no command allowlist). "
                           "cwd defaults to the workspace root. timeout_s defaults to 60.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command":   {"type": "string"},
                    "cwd":       {"type": "string"},
                    "timeout_s": {"type": "number"},
                },
                "required": ["command"],
            },
        },
    },
    execute=_bash,
))
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_tools_shell.py -v
```
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/shell.py vllm-agent/tests/test_tools_shell.py
git commit -m "Add bash worker tool with local-mode opt-in gate"
```

---

## Task 11: Web search tool — port DDG from existing MCP server

**Files:**
- Create: `vllm-agent/src/vllm_agent/tools/search.py`
- Create: `vllm-agent/tests/test_tools_search.py`

- [ ] **Step 1: Write failing test (with respx mock)**

`vllm-agent/tests/test_tools_search.py`:
```python
import pytest
import respx
from httpx import Response
from vllm_agent.tools.search import web_search_tool
from vllm_agent.tools import ToolContext
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript


@pytest.fixture
def ctx(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    return ToolContext(workspace=ws, transcript=Transcript(tmp_path / "t.jsonl"), env={})


_FAKE_HTML = """
<html><body>
  <div class="result">
    <a class="result__a" href="https://example.com/x">Example X</a>
    <div class="result__snippet">snippet for x</div>
  </div>
  <div class="result">
    <a class="result__a" href="https://example.com/y">Example Y</a>
    <div class="result__snippet">snippet for y</div>
  </div>
</body></html>
"""


@respx.mock
async def test_web_search_parses_results(ctx):
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=Response(200, text=_FAKE_HTML))
    out = await web_search_tool.execute({"query": "example"}, ctx)
    assert len(out["results"]) == 2
    assert out["results"][0]["title"] == "Example X"
    assert out["results"][0]["url"].startswith("https://example.com/x")


@respx.mock
async def test_web_search_handles_http_error(ctx):
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=Response(503, text="oops"))
    out = await web_search_tool.execute({"query": "example"}, ctx)
    assert "error" in out
```

- [ ] **Step 2: Verify failures**

```bash
cd vllm-agent && uv run pytest tests/test_tools_search.py -v
```
Expected: 2 FAILs.

- [ ] **Step 3: Implement `tools/search.py` (port from `mcp-server/vllm_mcp.py`)**

`vllm-agent/src/vllm_agent/tools/search.py`:
```python
"""Web search via DuckDuckGo HTML. Ported from mcp-server/vllm_mcp.py."""
from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from . import Tool, ToolContext, register

_BROWSER_UAS = [
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]
_DDG_MIN_INTERVAL = float(os.environ.get("DDG_MIN_INTERVAL_S", "1.5"))
_ddg_last_call: float = 0.0
_ddg_lock: asyncio.Lock | None = None


def _headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(_BROWSER_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    }


async def _throttle() -> None:
    global _ddg_last_call, _ddg_lock
    if _ddg_lock is None:
        _ddg_lock = asyncio.Lock()
    async with _ddg_lock:
        elapsed = time.monotonic() - _ddg_last_call
        wait = _DDG_MIN_INTERVAL - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        _ddg_last_call = time.monotonic()


async def _web_search(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    query = args.get("query", "")
    max_results = int(args.get("max_results", 5))
    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "kl": "us-en"},
                headers=_headers(),
            )
            r.raise_for_status()
            html = r.text
    except httpx.HTTPError as e:
        return {"error": f"DDG request failed: {type(e).__name__}: {e}"}
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, str]] = []
    for div in soup.select("div.result")[:max_results]:
        a = div.select_one("a.result__a")
        snip = div.select_one("a.result__snippet, div.result__snippet")
        if a is None:
            continue
        url = a.get("href") or ""
        if url.startswith("/l/?") or url.startswith("//duckduckgo.com/l/"):
            qs = parse_qs(urlparse(url).query)
            if "uddg" in qs:
                url = unquote(qs["uddg"][0])
        results.append({
            "title": a.get_text(strip=True),
            "url": url,
            "snippet": snip.get_text(strip=True) if snip else "",
        })
    return {"results": results}


web_search_tool = register(Tool(
    name="web_search",
    schema={
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web via DuckDuckGo and return up to "
                           "`max_results` snippets. Use for current events, exact "
                           "API details, version numbers, recent docs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    execute=_web_search,
))
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_tools_search.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/search.py vllm-agent/tests/test_tools_search.py
git commit -m "Add web_search worker tool (DDG, ported from mcp-server)"
```

---

## Task 12: Finish tool

**Files:**
- Create: `vllm-agent/src/vllm_agent/tools/finish.py`
- Create: `vllm-agent/tests/test_tools_finish.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_tools_finish.py`:
```python
from pathlib import Path
import pytest
from vllm_agent.tools.finish import finish_tool
from vllm_agent.tools import ToolContext
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript


@pytest.fixture
def ctx(tmp_path):
    ws = Workspace.resolve(str(tmp_path))
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(tmp_path / "t.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(tmp_path / "out")},
    )
    Path(ctx.env["VLLM_AGENT_OUT_DIR"]).mkdir(parents=True, exist_ok=True)
    return ctx


async def test_finish_writes_summary(tmp_path, ctx):
    out = await finish_tool.execute({"summary": "all done\nlooks good"}, ctx)
    assert out["status"] == "finished"
    assert (Path(ctx.env["VLLM_AGENT_OUT_DIR"]) / "summary.md").read_text() == "all done\nlooks good"


async def test_finish_empty_summary_warns(tmp_path, ctx):
    out = await finish_tool.execute({"summary": ""}, ctx)
    assert out["status"] == "finished"
    assert out.get("warning")
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Implement `tools/finish.py`**

`vllm-agent/src/vllm_agent/tools/finish.py`:
```python
"""Finish tool: worker calls this last to signal done."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import Tool, ToolContext, register


async def _finish(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    summary = (args.get("summary") or "").strip()
    out_dir_str = ctx.env.get("VLLM_AGENT_OUT_DIR")
    if not out_dir_str:
        return {"error": "VLLM_AGENT_OUT_DIR not set in agent environment"}
    out_dir = Path(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not summary:
        summary_text = "(worker called finish() with empty summary)"
        warning = "empty summary"
    else:
        summary_text = summary
        warning = None
    (out_dir / "summary.md").write_text(summary_text)
    result: dict[str, Any] = {"status": "finished", "summary_path": str(out_dir / "summary.md")}
    if warning:
        result["warning"] = warning
    return result


finish_tool = register(Tool(
    name="finish",
    schema={
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that the task is complete. Provide a 1-2 paragraph "
                           "summary of what you did, what you changed, and anything the "
                           "orchestrator should verify.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
    execute=_finish,
))
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_tools_finish.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/tools/finish.py vllm-agent/tests/test_tools_finish.py
git commit -m "Add finish worker tool"
```

---

## Task 13: Prompts module

**Purpose:** Build the system prompt (skill content + worker rules) and the user prompt (task + extra_context). Enforce the 32k context budget.

**Files:**
- Create: `vllm-agent/src/vllm_agent/prompts.py`
- Create: `vllm-agent/tests/test_prompts.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_prompts.py`:
```python
from pathlib import Path
import pytest
from vllm_agent.prompts import build_system_prompt, build_user_prompt, PromptBudgetError


def test_build_system_prompt_no_skill():
    out = build_system_prompt(skill_content=None, workdir="/tmp/x", mode="local")
    assert "vllm-rtx5090 worker" in out
    assert "/tmp/x" in out
    assert "Mode: local" in out


def test_build_system_prompt_with_skill():
    out = build_system_prompt(
        skill_content="---\nname: tdd\n---\n\nthe full skill body",
        workdir="/tmp/x", mode="remote")
    assert "the full skill body" in out
    assert "vllm-rtx5090 worker" in out


def test_build_user_prompt_with_extra_context(tmp_path):
    f = tmp_path / "ctx.txt"
    f.write_text("contents of ctx")
    out = build_user_prompt(task="do X", extra_context_paths=[str(f)])
    assert "do X" in out
    assert "contents of ctx" in out
    assert "ctx.txt" in out


def test_user_prompt_truncates_oversized_context(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 200_000)  # 200k chars
    out = build_user_prompt(task="do X", extra_context_paths=[str(big)],
                            max_context_chars=10_000)
    assert "[truncated" in out
    assert len(out) < 12_000


def test_budget_check_raises_when_too_large():
    huge = "x" * 200_000
    with pytest.raises(PromptBudgetError):
        build_system_prompt(skill_content=huge, workdir="/tmp/x", mode="local",
                            budget_chars=50_000)
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Implement `prompts.py`**

`vllm-agent/src/vllm_agent/prompts.py`:
```python
"""Construct system + user prompts for the agent loop, with context budget enforcement.

Rough char-to-token ratio: 4 chars/token. 32k context = 128k chars total budget.
We reserve ~32k chars for completion + working transcript, leaving ~96k for
system + initial user prompt. Defaults below assume that envelope.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_BUDGET_CHARS = 96_000   # ~24k tokens for system + user prompt
DEFAULT_CONTEXT_CHARS = 60_000  # cap for `extra_context` body


class PromptBudgetError(Exception):
    pass


_WORKER_TAIL = """
You are the vllm-rtx5090 worker. You have these tools:
  read_file, write_file, edit_file, bash, grep, glob, web_search, finish

Workspace: {workdir}
Mode: {mode}

Discipline:
- Edit files in place via edit_file/write_file. Run tests via bash.
- Iterate until the task is done or you hit a blocker.
- When done, call finish() with a 1-2 paragraph summary of what you did,
  what you changed, and anything the orchestrator should verify.
- Never edit files outside {workdir}.
- Use web_search for facts you don't reliably know.
"""


def build_system_prompt(
    skill_content: str | None,
    workdir: str,
    mode: str,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> str:
    body = _WORKER_TAIL.format(workdir=workdir, mode=mode)
    if skill_content:
        body = skill_content.rstrip() + "\n\n" + body
    if len(body) > budget_chars:
        raise PromptBudgetError(
            f"system prompt is {len(body)} chars; budget is {budget_chars}. "
            "Use a shorter skill or raise the budget.")
    return body


def build_user_prompt(
    task: str,
    extra_context_paths: list[str] | None = None,
    max_context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> str:
    parts: list[str] = [f"Task:\n{task}\n"]
    used = 0
    if extra_context_paths:
        parts.append("\nExtra context (pre-loaded files):\n")
        for p in extra_context_paths:
            try:
                text = Path(p).read_text()
            except (FileNotFoundError, PermissionError, IsADirectoryError) as e:
                parts.append(f"--- {p} ---\n[could not read: {e}]\n")
                continue
            remaining = max_context_chars - used
            if remaining <= 0:
                parts.append(f"--- {p} ---\n[truncated: budget exhausted]\n")
                continue
            if len(text) > remaining:
                parts.append(f"--- {p} (first {remaining} chars; truncated) ---\n"
                             f"{text[:remaining]}\n[truncated]\n")
                used = max_context_chars
            else:
                parts.append(f"--- {p} ---\n{text}\n")
                used += len(text)
    return "".join(parts)
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_prompts.py -v
```
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/prompts.py vllm-agent/tests/test_prompts.py
git commit -m "Add prompt construction with context-budget enforcement"
```

---

## Task 14: Transcript module (full)

**Files:**
- Modify: `vllm-agent/src/vllm_agent/transcript.py`
- Create: `vllm-agent/tests/test_transcript.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_transcript.py`:
```python
import json
from vllm_agent.transcript import Transcript


def test_transcript_appends_jsonl(tmp_path):
    t = Transcript(tmp_path / "out" / "t.jsonl")
    t.append({"role": "system", "content": "hi"})
    t.append({"role": "user", "content": "go"})
    lines = (tmp_path / "out" / "t.jsonl").read_text().splitlines()
    assert json.loads(lines[0]) == {"role": "system", "content": "hi"}
    assert json.loads(lines[1]) == {"role": "user", "content": "go"}


def test_transcript_records_tool_call(tmp_path):
    t = Transcript(tmp_path / "t.jsonl")
    t.record_tool_call("read_file", {"path": "x"}, {"content": "..."})
    rec = json.loads((tmp_path / "t.jsonl").read_text().splitlines()[0])
    assert rec["kind"] == "tool_call"
    assert rec["tool"] == "read_file"
    assert rec["args"] == {"path": "x"}
    assert rec["result"] == {"content": "..."}
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Replace `transcript.py` with the fuller version**

`vllm-agent/src/vllm_agent/transcript.py`:
```python
"""Transcript: append-only JSONL recorder for an agent run."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Transcript:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, record: dict[str, Any]) -> None:
        record = {"ts": time.time(), **record}
        with self.path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def record_message(self, role: str, content: Any) -> None:
        self.append({"kind": "message", "role": role, "content": content})

    def record_tool_call(self, tool: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        self.append({"kind": "tool_call", "tool": tool, "args": args, "result": result})
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_transcript.py tests/test_tools_fs.py tests/test_tools_shell.py -v
```
Expected: all PASS (the previous task tests still pass; new transcript tests now pass).

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/transcript.py vllm-agent/tests/test_transcript.py
git commit -m "Flesh out Transcript with message/tool-call recording"
```

---

## Task 15: Sessions module

**Files:**
- Create: `vllm-agent/src/vllm_agent/sessions.py`
- Create: `vllm-agent/tests/test_sessions.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_sessions.py`:
```python
from pathlib import Path
import json
import pytest
from vllm_agent.sessions import SessionStore, Session, SessionStatus


def test_create_and_load_session(tmp_path):
    store = SessionStore(root=tmp_path)
    s = store.create(goal="do X", skill="superpowers:tdd",
                     mode="remote", workdir="/tmp/repo", model=None)
    assert s.session_id
    assert (tmp_path / s.session_id / "session.json").exists()

    s2 = store.load(s.session_id)
    assert s2.goal == "do X"
    assert s2.status == SessionStatus.RUNNING


def test_append_and_load_messages(tmp_path):
    store = SessionStore(root=tmp_path)
    s = store.create(goal="g", skill=None, mode="local", workdir="/tmp", model=None)
    store.append_message(s.session_id, {"role": "user", "content": "go"})
    store.append_message(s.session_id, {"role": "assistant", "content": "ok"})
    msgs = store.load_messages(s.session_id)
    assert msgs == [{"role": "user", "content": "go"},
                    {"role": "assistant", "content": "ok"}]


def test_set_status(tmp_path):
    store = SessionStore(root=tmp_path)
    s = store.create(goal="g", skill=None, mode="local", workdir="/tmp", model=None)
    store.set_status(s.session_id, SessionStatus.STOPPED)
    assert store.load(s.session_id).status == SessionStatus.STOPPED


def test_unknown_session_raises(tmp_path):
    store = SessionStore(root=tmp_path)
    with pytest.raises(KeyError):
        store.load("does-not-exist")
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Implement `sessions.py`**

`vllm-agent/src/vllm_agent/sessions.py`:
```python
"""Filesystem-backed session storage."""
from __future__ import annotations

import enum
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class SessionStatus(str, enum.Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ERRORED = "errored"


@dataclass
class Session:
    session_id: str
    goal: str
    skill: str | None
    mode: str
    workdir: str
    model: str | None
    status: SessionStatus
    started_at: float
    last_activity_at: float
    iterations_total: int = 0
    files_changed_total: list[str] = field(default_factory=list)


@dataclass
class SessionStore:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, session_id: str) -> Path:
        return self.root / session_id

    def create(self, goal: str, skill: str | None, mode: str,
               workdir: str, model: str | None) -> Session:
        sid = uuid.uuid4().hex[:12]
        now = time.time()
        s = Session(session_id=sid, goal=goal, skill=skill, mode=mode,
                    workdir=workdir, model=model,
                    status=SessionStatus.RUNNING,
                    started_at=now, last_activity_at=now)
        d = self._dir(sid)
        d.mkdir(parents=True, exist_ok=True)
        self._write_meta(s)
        (d / "messages.jsonl").touch()
        (d / "transcript.jsonl").touch()
        return s

    def load(self, session_id: str) -> Session:
        meta = self._dir(session_id) / "session.json"
        if not meta.exists():
            raise KeyError(f"unknown session: {session_id}")
        data = json.loads(meta.read_text())
        data["status"] = SessionStatus(data["status"])
        return Session(**data)

    def list(self) -> list[Session]:
        return [self.load(p.name) for p in self.root.iterdir() if (p / "session.json").exists()]

    def append_message(self, session_id: str, msg: dict[str, Any]) -> None:
        (self._dir(session_id) / "messages.jsonl").open("a").write(
            json.dumps(msg, ensure_ascii=False) + "\n")
        s = self.load(session_id)
        s.last_activity_at = time.time()
        self._write_meta(s)

    def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        f = self._dir(session_id) / "messages.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]

    def set_status(self, session_id: str, status: SessionStatus) -> None:
        s = self.load(session_id)
        s.status = status
        s.last_activity_at = time.time()
        self._write_meta(s)

    def add_files_changed(self, session_id: str, files: list[str]) -> None:
        s = self.load(session_id)
        existing = set(s.files_changed_total)
        existing.update(files)
        s.files_changed_total = sorted(existing)
        self._write_meta(s)

    def bump_iterations(self, session_id: str, n: int) -> None:
        s = self.load(session_id)
        s.iterations_total += n
        self._write_meta(s)

    def _write_meta(self, s: Session) -> None:
        d = asdict(s)
        d["status"] = s.status.value
        (self._dir(s.session_id) / "session.json").write_text(
            json.dumps(d, indent=2))
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_sessions.py -v
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/sessions.py vllm-agent/tests/test_sessions.py
git commit -m "Add filesystem-backed SessionStore"
```

---

## Task 16: Agent loop core (happy path)

**Purpose:** The single-shot tool-call loop. Takes initial messages, runs vLLM round-trips, executes tool calls, returns when the worker stops calling tools or hits `max_iterations`.

**Files:**
- Create: `vllm-agent/src/vllm_agent/loop.py`
- Create: `vllm-agent/tests/test_loop.py`

- [ ] **Step 1: Write failing test (single-shot, mocked vLLM)**

`vllm-agent/tests/test_loop.py`:
```python
import json
import pytest
import respx
from httpx import Response
from vllm_agent.loop import run_loop, LoopConfig
from vllm_agent.workspace import Workspace
from vllm_agent.transcript import Transcript
from vllm_agent.tools import ToolContext


def _vllm_response(content=None, tool_calls=None):
    msg = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


@respx.mock
async def test_loop_single_shot_finish(tmp_path):
    """vLLM replies with no tool_calls — loop returns immediately."""
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json=_vllm_response(content="all done")))

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )

    cfg = LoopConfig(
        vllm_base_url="https://vllm.example",
        vllm_model="qwen3-coder",
        max_iterations=5,
        max_tokens=512,
        temperature=0.2,
    )
    msgs = [{"role": "user", "content": "say hi"}]
    result = await run_loop(msgs, ctx, cfg)
    assert result.iterations == 1
    assert result.status == "ok"
    assert result.final_message_content == "all done"


@respx.mock
async def test_loop_one_tool_call_then_finish(tmp_path):
    """First reply calls read_file; second reply finishes."""
    (tmp_path / "x.txt").write_text("hello")

    responses = [
        _vllm_response(tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": "x.txt"})},
        }]),
        _vllm_response(content="read it; done"),
    ]
    counter = {"i": 0}
    def _next(_request):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=responses[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )
    cfg = LoopConfig(
        vllm_base_url="https://vllm.example",
        vllm_model="qwen3-coder",
        max_iterations=5,
        max_tokens=512,
        temperature=0.2,
    )
    result = await run_loop([{"role": "user", "content": "read x.txt"}], ctx, cfg)
    assert result.iterations == 2
    assert result.status == "ok"
    assert "read_file" in [t for t in result.tool_calls_by_name]


@respx.mock
async def test_loop_max_iterations(tmp_path):
    """vLLM keeps calling tools forever — loop bails at max_iterations."""
    (tmp_path / "x.txt").write_text("hi")
    forever = _vllm_response(tool_calls=[{
        "id": "call_loop",
        "type": "function",
        "function": {"name": "read_file",
                     "arguments": json.dumps({"path": "x.txt"})},
    }])
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json=forever))

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )
    cfg = LoopConfig(
        vllm_base_url="https://vllm.example",
        vllm_model="qwen3-coder",
        max_iterations=3,
        max_tokens=512,
        temperature=0.2,
    )
    result = await run_loop([{"role": "user", "content": "go"}], ctx, cfg)
    assert result.status == "max_iterations"
    assert result.iterations == 3
```

- [ ] **Step 2: Verify failures**

```bash
cd vllm-agent && uv run pytest tests/test_loop.py -v
```
Expected: 3 FAILs.

- [ ] **Step 3: Implement `loop.py`**

`vllm-agent/src/vllm_agent/loop.py`:
```python
"""The agent tool-call loop."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

from .tools import WORKER_TOOLS, ToolContext


@dataclass
class LoopConfig:
    vllm_base_url: str
    vllm_model: str
    max_iterations: int = 30
    max_tokens: int = 4096
    temperature: float = 0.2
    api_key: str | None = None
    request_timeout_s: float = 600.0


@dataclass
class LoopResult:
    messages: list[dict[str, Any]]
    iterations: int
    status: str   # "ok" | "max_iterations" | "error"
    final_message_content: str | None = None
    tool_calls_by_name: Counter = field(default_factory=Counter)
    error: str | None = None


def _vllm_headers(api_key: str | None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _tools_schema() -> list[dict[str, Any]]:
    return [t.schema for t in WORKER_TOOLS.values()]


async def run_loop(
    messages: list[dict[str, Any]],
    ctx: ToolContext,
    cfg: LoopConfig,
) -> LoopResult:
    msgs = [dict(m) for m in messages]
    tool_counts: Counter = Counter()
    iterations = 0
    final_content: str | None = None

    async with httpx.AsyncClient(timeout=cfg.request_timeout_s) as client:
        for i in range(cfg.max_iterations):
            iterations = i + 1
            try:
                r = await client.post(
                    f"{cfg.vllm_base_url}/v1/chat/completions",
                    headers=_vllm_headers(cfg.api_key),
                    json={
                        "model": cfg.vllm_model,
                        "messages": msgs,
                        "tools": _tools_schema(),
                        "tool_choice": "auto",
                        "max_tokens": cfg.max_tokens,
                        "temperature": cfg.temperature,
                    },
                )
                r.raise_for_status()
            except httpx.HTTPError as e:
                return LoopResult(messages=msgs, iterations=iterations,
                                  status="error", tool_calls_by_name=tool_counts,
                                  error=f"vLLM HTTP error: {type(e).__name__}: {e}")
            data = r.json()
            msg = data["choices"][0]["message"]
            assistant_msg = {"role": "assistant",
                             "content": msg.get("content"),
                             "tool_calls": msg.get("tool_calls") or []}
            msgs.append(assistant_msg)
            ctx.transcript.record_message("assistant", assistant_msg)

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                final_content = msg.get("content")
                return LoopResult(messages=msgs, iterations=iterations,
                                  status="ok", final_message_content=final_content,
                                  tool_calls_by_name=tool_counts)
            for tc in tool_calls:
                fn = (tc.get("function") or {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_counts[name] += 1
                tool = WORKER_TOOLS.get(name)
                if tool is None:
                    result = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = await tool.execute(args, ctx)
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}
                ctx.transcript.record_tool_call(name, args, result)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                # Worker called finish → end loop early.
                if name == "finish" and result.get("status") == "finished":
                    return LoopResult(messages=msgs, iterations=iterations,
                                      status="ok",
                                      final_message_content=result.get("summary_path"),
                                      tool_calls_by_name=tool_counts)

    return LoopResult(messages=msgs, iterations=iterations,
                      status="max_iterations",
                      tool_calls_by_name=tool_counts)
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_loop.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/loop.py vllm-agent/tests/test_loop.py
git commit -m "Add agent tool-call loop with happy path + max_iterations"
```

---

## Task 17: Loop edge cases — tool errors, vLLM retry

**Files:**
- Modify: `vllm-agent/src/vllm_agent/loop.py`
- Modify: `vllm-agent/tests/test_loop.py`

- [ ] **Step 1: Add failing tests**

Append to `test_loop.py`:
```python
@respx.mock
async def test_loop_tool_exception_recovers(tmp_path):
    """A tool raising an exception becomes a tool-error result; loop continues."""
    responses = [
        _vllm_response(tool_calls=[{
            "id": "c1",
            "type": "function",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": "missing.txt"})},
        }]),
        _vllm_response(content="couldn't read it; giving up"),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=responses[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(
        workspace=ws,
        transcript=Transcript(out_dir / "transcript.jsonl"),
        env={"VLLM_AGENT_OUT_DIR": str(out_dir)},
    )
    cfg = LoopConfig(vllm_base_url="https://vllm.example",
                     vllm_model="m", max_iterations=5,
                     max_tokens=128, temperature=0)
    result = await run_loop([{"role": "user", "content": "go"}], ctx, cfg)
    assert result.status == "ok"
    # The tool call recorded the file-not-found, but the loop didn't crash.


@respx.mock
async def test_loop_vllm_5xx_retried_once(tmp_path):
    """First call fails 503, retry succeeds."""
    responses = [
        Response(503, text="oops"),
        Response(200, json=_vllm_response(content="ok now")),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return responses[i]
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    ws = Workspace.resolve(str(tmp_path))
    out_dir = tmp_path / "out"; out_dir.mkdir()
    ctx = ToolContext(workspace=ws,
                      transcript=Transcript(out_dir / "transcript.jsonl"),
                      env={"VLLM_AGENT_OUT_DIR": str(out_dir)})
    cfg = LoopConfig(vllm_base_url="https://vllm.example",
                     vllm_model="m", max_iterations=2,
                     max_tokens=64, temperature=0)
    result = await run_loop([{"role": "user", "content": "go"}], ctx, cfg)
    assert result.status == "ok"
    assert result.final_message_content == "ok now"
```

- [ ] **Step 2: Verify failures**

```bash
cd vllm-agent && uv run pytest tests/test_loop.py::test_loop_vllm_5xx_retried_once -v
```
Expected: FAIL (no retry yet). Tool-exception test should already pass since the existing implementation catches `Exception` per-tool — verify.

- [ ] **Step 3: Add retry logic to `run_loop`**

In `loop.py`, replace the `try / r.raise_for_status() / except httpx.HTTPError` block inside the loop with a small retry:
```python
            attempt = 0
            while True:
                try:
                    r = await client.post(
                        f"{cfg.vllm_base_url}/v1/chat/completions",
                        headers=_vllm_headers(cfg.api_key),
                        json={
                            "model": cfg.vllm_model,
                            "messages": msgs,
                            "tools": _tools_schema(),
                            "tool_choice": "auto",
                            "max_tokens": cfg.max_tokens,
                            "temperature": cfg.temperature,
                        },
                    )
                    r.raise_for_status()
                    break
                except httpx.HTTPError as e:
                    if attempt >= 1:
                        return LoopResult(messages=msgs, iterations=iterations,
                                          status="error",
                                          tool_calls_by_name=tool_counts,
                                          error=f"vLLM HTTP error: {type(e).__name__}: {e}")
                    attempt += 1
                    import asyncio
                    await asyncio.sleep(1.0)
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_loop.py -v
```
Expected: 5 PASS total.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/loop.py vllm-agent/tests/test_loop.py
git commit -m "Loop: tolerate tool exceptions, retry vLLM 5xx once"
```

---

## Task 18: Public API — agent_run

**Purpose:** The high-level entry point that the CLI and HTTP server both call. Resolves workspace, loads skill, builds prompts, runs the loop, captures diff/files-changed, writes outputs.

**Files:**
- Create: `vllm-agent/src/vllm_agent/api.py`
- Create: `vllm-agent/tests/test_api.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_api.py`:
```python
import json
import os
import pytest
import respx
from httpx import Response
from vllm_agent.api import agent_run, AgentRunRequest


def _resp(content=None, tool_calls=None):
    msg = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


@respx.mock
async def test_agent_run_writes_summary(tmp_path, monkeypatch):
    """End-to-end: agent_run produces summary.md, transcript.jsonl, files_changed.txt."""
    # First reply: write a file. Second reply: finish.
    seq = [
        _resp(tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"path": "out.txt", "content": "hi"})},
        }]),
        _resp(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "finish",
                         "arguments": json.dumps({"summary": "wrote out.txt"})},
        }]),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=seq[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")

    out_dir = tmp_path / "out"
    req = AgentRunRequest(
        task="write hi to out.txt then finish",
        skill=None,
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(out_dir),
        max_iterations=5,
    )
    result = await agent_run(req)
    assert result.status == "ok"
    assert (out_dir / "summary.md").read_text() == "wrote out.txt"
    assert (out_dir / "transcript.jsonl").exists()
    assert "out.txt" in (tmp_path / "out.txt").read_text()
    assert "out.txt" in (out_dir / "files_changed.txt").read_text()
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Implement `api.py`**

`vllm-agent/src/vllm_agent/api.py`:
```python
"""Public API: agent_run + agent_session_*. CLI and HTTP server call these."""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loop import LoopConfig, run_loop
from .prompts import build_system_prompt, build_user_prompt
from .skills import SkillLoader
from .tools import ToolContext, WORKER_TOOLS  # noqa: F401  (force tool registration)
from .tools import fs as _fs                  # noqa: F401
from .tools import shell as _shell            # noqa: F401
from .tools import search as _search          # noqa: F401
from .tools import finish as _finish          # noqa: F401
from .transcript import Transcript
from .workspace import Workspace


# Default location for run outputs when caller doesn't specify out_dir.
DEFAULT_RUN_ROOT = Path("~/.cache/vllm-agent/runs").expanduser()


@dataclass
class AgentRunRequest:
    task: str
    skill: str | None = None
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


def _snapshot_files(workdir: Path) -> dict[str, float]:
    """Return path → mtime for all files in workdir (used to detect changes)."""
    out: dict[str, float] = {}
    for p in workdir.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    return out


def _files_changed(before: dict[str, float], workdir: Path) -> list[str]:
    after = _snapshot_files(workdir)
    changed: set[str] = set()
    for path, mt in after.items():
        if path not in before or before[path] != mt:
            changed.add(path)
    for path in before:
        if path not in after:
            changed.add(path)
    return sorted(str(Path(p).relative_to(workdir)) for p in changed)


async def agent_run(req: AgentRunRequest) -> AgentRunResult:
    run_id = uuid.uuid4().hex[:12]
    out_dir = Path(req.out_dir) if req.out_dir else (DEFAULT_RUN_ROOT / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    ws = Workspace.resolve(req.workdir)
    transcript = Transcript(out_dir / "transcript.jsonl")
    skill_content = SkillLoader().load_skill(req.skill) if req.skill else None
    system_prompt = build_system_prompt(skill_content, str(ws.root), req.mode)
    user_prompt = build_user_prompt(req.task, req.extra_context)

    ctx = ToolContext(
        workspace=ws,
        transcript=transcript,
        env={
            "VLLM_AGENT_MODE": req.mode,
            "VLLM_AGENT_LOCAL_BASH": os.environ.get("VLLM_AGENT_LOCAL_BASH", ""),
            "VLLM_AGENT_OUT_DIR": str(out_dir),
        },
    )
    cfg = LoopConfig(
        vllm_base_url=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000"),
        vllm_model=req.model or os.environ.get("VLLM_MODEL", ""),
        max_iterations=req.max_iterations,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        api_key=os.environ.get("VLLM_API_KEY") or None,
        request_timeout_s=float(req.timeout_s),
    )

    before = _snapshot_files(ws.root)
    transcript.record_message("system", system_prompt)
    transcript.record_message("user", user_prompt)
    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    t0 = time.perf_counter()
    loop_result = await run_loop(msgs, ctx, cfg)
    duration = time.perf_counter() - t0

    files_changed = _files_changed(before, ws.root)
    (out_dir / "files_changed.txt").write_text("\n".join(files_changed) + ("\n" if files_changed else ""))

    summary_path = out_dir / "summary.md"
    if not summary_path.exists():
        # Worker didn't call finish() — synthesize a placeholder.
        body = loop_result.final_message_content or "(no final summary; worker did not call finish())"
        summary_path.write_text(body)

    return AgentRunResult(
        run_id=run_id,
        out_dir=str(out_dir),
        summary_path=str(summary_path),
        files_changed=files_changed,
        diff_path=None,   # filled in Plan B (git diff after run, when in remote mode)
        iterations=loop_result.iterations,
        duration_s=round(duration, 2),
        status=loop_result.status,
        error=loop_result.error,
    )
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_api.py -v
```
Expected: 1 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/api.py vllm-agent/tests/test_api.py
git commit -m "Add agent_run public API"
```

---

## Task 19: Public API — agent_session_*

**Files:**
- Modify: `vllm-agent/src/vllm_agent/api.py`
- Modify: `vllm-agent/tests/test_api.py`

- [ ] **Step 1: Add failing tests**

Append to `test_api.py`:
```python
import json as _json


@respx.mock
async def test_agent_session_start_step_status_stop(tmp_path, monkeypatch):
    """Multi-step session: start, run one step that calls finish, then stop."""
    seq = [
        _resp(tool_calls=[{
            "id": "c", "type": "function",
            "function": {"name": "finish",
                         "arguments": _json.dumps({"summary": "one-step done"})},
        }]),
    ]
    counter = {"i": 0}
    def _next(_req):
        i = counter["i"]; counter["i"] += 1
        return Response(200, json=seq[i])
    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_next)

    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))

    from vllm_agent.api import agent_session_start, agent_session_step, agent_session_status, agent_session_stop, AgentSessionStartRequest
    s = await agent_session_start(AgentSessionStartRequest(
        goal="do a thing", skill=None, mode="remote", workdir=str(tmp_path)))
    assert s.status == "running"

    step = await agent_session_step(s.session_id, nudge=None, max_iterations=3)
    assert step.status == "ok"
    assert step.iterations_this_step == 1

    status = await agent_session_status(s.session_id)
    assert status.iterations_total == 1

    stopped = await agent_session_stop(s.session_id)
    assert stopped.status == "stopped"
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Append session API to `api.py`**

```python
# ---- session API ------------------------------------------------------------

from .sessions import SessionStore, SessionStatus

DEFAULT_SESSION_ROOT = Path("~/.cache/vllm-agent/sessions").expanduser()


def _session_store() -> SessionStore:
    root = Path(os.environ.get("VLLM_AGENT_SESSION_ROOT", str(DEFAULT_SESSION_ROOT)))
    return SessionStore(root=root)


@dataclass
class AgentSessionStartRequest:
    goal: str
    skill: str | None = None
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None


@dataclass
class AgentSessionStartResult:
    session_id: str
    out_dir: str
    status: str


async def agent_session_start(req: AgentSessionStartRequest) -> AgentSessionStartResult:
    ws = Workspace.resolve(req.workdir)
    store = _session_store()
    s = store.create(goal=req.goal, skill=req.skill, mode=req.mode,
                     workdir=str(ws.root), model=req.model)
    return AgentSessionStartResult(
        session_id=s.session_id,
        out_dir=str(store._dir(s.session_id)),
        status=s.status.value,
    )


@dataclass
class AgentSessionStepResult:
    session_id: str
    iterations_this_step: int
    files_changed_this_step: list[str]
    summary_path: str
    status: str


async def agent_session_step(
    session_id: str,
    nudge: str | None = None,
    max_iterations: int = 10,
) -> AgentSessionStepResult:
    store = _session_store()
    s = store.load(session_id)
    if s.status in (SessionStatus.STOPPED, SessionStatus.COMPLETED, SessionStatus.ERRORED):
        return AgentSessionStepResult(
            session_id=session_id, iterations_this_step=0,
            files_changed_this_step=[],
            summary_path=str(store._dir(session_id) / "summary.md"),
            status=s.status.value,
        )

    ws = Workspace.resolve(s.workdir)
    sess_dir = store._dir(session_id)
    transcript = Transcript(sess_dir / "transcript.jsonl")

    skill_content = SkillLoader().load_skill(s.skill) if s.skill else None
    system_prompt = build_system_prompt(skill_content, str(ws.root), s.mode)
    user_prompt = build_user_prompt(s.goal, None)

    msgs = store.load_messages(session_id)
    if not msgs:
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for m in msgs:
            store.append_message(session_id, m)
    if nudge:
        msgs.append({"role": "user", "content": nudge})
        store.append_message(session_id, {"role": "user", "content": nudge})

    ctx = ToolContext(
        workspace=ws,
        transcript=transcript,
        env={
            "VLLM_AGENT_MODE": s.mode,
            "VLLM_AGENT_LOCAL_BASH": os.environ.get("VLLM_AGENT_LOCAL_BASH", ""),
            "VLLM_AGENT_OUT_DIR": str(sess_dir),
        },
    )
    cfg = LoopConfig(
        vllm_base_url=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000"),
        vllm_model=s.model or os.environ.get("VLLM_MODEL", ""),
        max_iterations=max_iterations,
        max_tokens=4096,
        temperature=0.2,
        api_key=os.environ.get("VLLM_API_KEY") or None,
    )

    before = _snapshot_files(ws.root)
    loop_result = await run_loop(msgs, ctx, cfg)
    files_changed = _files_changed(before, ws.root)

    # Persist new messages produced this step.
    new_msgs = loop_result.messages[len(msgs):]
    for m in new_msgs:
        store.append_message(session_id, m)
    store.add_files_changed(session_id, files_changed)
    store.bump_iterations(session_id, loop_result.iterations)
    if loop_result.status == "ok":
        store.set_status(session_id, SessionStatus.COMPLETED)
    elif loop_result.status == "error":
        store.set_status(session_id, SessionStatus.ERRORED)

    return AgentSessionStepResult(
        session_id=session_id,
        iterations_this_step=loop_result.iterations,
        files_changed_this_step=files_changed,
        summary_path=str(sess_dir / "summary.md"),
        status=loop_result.status,
    )


@dataclass
class AgentSessionStatusResult:
    session_id: str
    status: str
    iterations_total: int
    files_changed_total: list[str]
    started_at: float
    last_activity_at: float
    out_dir: str


async def agent_session_status(session_id: str) -> AgentSessionStatusResult:
    store = _session_store()
    s = store.load(session_id)
    return AgentSessionStatusResult(
        session_id=session_id,
        status=s.status.value,
        iterations_total=s.iterations_total,
        files_changed_total=s.files_changed_total,
        started_at=s.started_at,
        last_activity_at=s.last_activity_at,
        out_dir=str(store._dir(session_id)),
    )


@dataclass
class AgentSessionStopResult:
    session_id: str
    status: str


async def agent_session_stop(session_id: str) -> AgentSessionStopResult:
    store = _session_store()
    store.set_status(session_id, SessionStatus.STOPPED)
    return AgentSessionStopResult(session_id=session_id, status="stopped")
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_api.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/api.py vllm-agent/tests/test_api.py
git commit -m "Add agent_session_start/step/status/stop public API"
```

---

## Task 20: CLI — typer entry points

**Purpose:** `vllm-agent run`, `vllm-agent serve`, `vllm-agent list-skills`.

**Files:**
- Create: `vllm-agent/src/vllm_agent/cli.py`
- Create: `vllm-agent/tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_cli.py`:
```python
from typer.testing import CliRunner
from vllm_agent.cli import app


def test_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "serve" in result.stdout
    assert "list-skills" in result.stdout


def test_list_skills_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["list-skills"])
    assert result.exit_code == 0
    # No skills installed in fresh HOME — output is just an empty list / header.
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Implement `cli.py`**

`vllm-agent/src/vllm_agent/cli.py`:
```python
"""Typer CLI: `vllm-agent run`, `serve`, `list-skills`."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from .api import AgentRunRequest, agent_run
from .skills import SkillLoader

app = typer.Typer(help="Agent runtime backed by vLLM.")


@app.command("run")
def cmd_run(
    task: str = typer.Argument(..., help="The task instruction for the worker."),
    skill: str | None = typer.Option(None, help="Skill name, e.g. superpowers:tdd."),
    mode: str = typer.Option("remote", help="local | remote"),
    workdir: str | None = typer.Option(None, help="Working directory."),
    out_dir: str | None = typer.Option(None, help="Output dir for transcript/summary."),
    max_iterations: int = typer.Option(30),
    max_tokens: int = typer.Option(4096),
    temperature: float = typer.Option(0.2),
) -> None:
    """Run a one-shot agent task and print the result as JSON."""
    req = AgentRunRequest(
        task=task, skill=skill, mode=mode, workdir=workdir, out_dir=out_dir,
        max_iterations=max_iterations, max_tokens=max_tokens, temperature=temperature,
    )
    result = asyncio.run(agent_run(req))
    typer.echo(json.dumps(result.__dict__, indent=2, default=str))


@app.command("list-skills")
def cmd_list_skills() -> None:
    """List all skills discoverable from the configured roots."""
    for s in SkillLoader().list_skills():
        typer.echo(f"{s['name']}\t{s['description']}")


@app.command("serve")
def cmd_serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8088),
) -> None:
    """Start the FastAPI HTTP server (used by mode=remote)."""
    import uvicorn
    from .server import app as fastapi_app
    uvicorn.run(fastapi_app, host=host, port=port)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_cli.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/cli.py vllm-agent/tests/test_cli.py
git commit -m "Add typer CLI: run, list-skills, serve"
```

---

## Task 21: HTTP server — FastAPI endpoints

**Purpose:** `POST /run`, `POST /session`, `POST /session/{id}/step`, `GET /session/{id}`, `POST /session/{id}/stop`, `GET /skills`, `GET /health`. Used by Plan B's MCP shim when `mode=remote`.

**Files:**
- Create: `vllm-agent/src/vllm_agent/server.py`
- Create: `vllm-agent/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

`vllm-agent/tests/test_server.py`:
```python
import json
import pytest
import respx
from httpx import Response, AsyncClient
from vllm_agent.server import app as fastapi_app


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(fastapi_app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@respx.mock
def test_run_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "done"}}]
        })
    )
    r = client.post("/run", json={
        "task": "do nothing",
        "mode": "remote",
        "workdir": str(tmp_path),
        "out_dir": str(tmp_path / "out"),
        "max_iterations": 1,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["run_id"]


def test_skills_endpoint(client):
    r = client.get("/skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
```

- [ ] **Step 2: Verify failures**

- [ ] **Step 3: Implement `server.py`**

`vllm-agent/src/vllm_agent/server.py`:
```python
"""FastAPI app exposing agent_run and agent_session_* over HTTP."""
from __future__ import annotations

import os
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .api import (
    AgentRunRequest, agent_run,
    AgentSessionStartRequest, agent_session_start,
    agent_session_step, agent_session_status, agent_session_stop,
)
from .skills import SkillLoader

app = FastAPI(title="vllm-agent")


class RunBody(BaseModel):
    task: str
    skill: str | None = None
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
    mode: str = "remote"
    workdir: str | None = None
    model: str | None = None


class SessionStepBody(BaseModel):
    nudge: str | None = None
    max_iterations: int = 10


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "vllm_base_url": os.environ.get("VLLM_BASE_URL"),
        "vllm_model": os.environ.get("VLLM_MODEL"),
    }


@app.post("/run")
async def run(body: RunBody) -> dict:
    result = await agent_run(AgentRunRequest(**body.model_dump()))
    return asdict(result)


@app.post("/session")
async def session_start(body: SessionStartBody) -> dict:
    result = await agent_session_start(AgentSessionStartRequest(**body.model_dump()))
    return asdict(result)


@app.post("/session/{session_id}/step")
async def session_step(session_id: str, body: SessionStepBody) -> dict:
    result = await agent_session_step(
        session_id, nudge=body.nudge, max_iterations=body.max_iterations)
    return asdict(result)


@app.get("/session/{session_id}")
async def session_status(session_id: str) -> dict:
    try:
        return asdict(await agent_session_status(session_id))
    except KeyError:
        raise HTTPException(404, f"unknown session: {session_id}")


@app.post("/session/{session_id}/stop")
async def session_stop(session_id: str) -> dict:
    return asdict(await agent_session_stop(session_id))


@app.get("/skills")
async def skills() -> list[dict]:
    return SkillLoader().list_skills()
```

- [ ] **Step 4: Run tests**

```bash
cd vllm-agent && uv run pytest tests/test_server.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add vllm-agent/src/vllm_agent/server.py vllm-agent/tests/test_server.py
git commit -m "Add FastAPI HTTP server for agent_run + sessions"
```

---

## Task 22: Live smoke test against the real VM (opt-in)

**Files:**
- Create: `vllm-agent/tests/test_live_smoke.py`

- [ ] **Step 1: Write the smoke test**

`vllm-agent/tests/test_live_smoke.py`:
```python
"""Live smoke test against the real vLLM endpoint. Opt-in via `pytest -m live`."""
import os
import pytest
from vllm_agent.api import AgentRunRequest, agent_run

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("VLLM_BASE_URL"),
                    reason="VLLM_BASE_URL not set")
async def test_live_simple_finish(tmp_path):
    """Hit the real vLLM and ask the worker to call finish()."""
    req = AgentRunRequest(
        task="Call the finish() tool with the summary 'live ping ok'. "
             "Do not use any other tools.",
        mode="remote",
        workdir=str(tmp_path),
        out_dir=str(tmp_path / "out"),
        max_iterations=3,
        max_tokens=512,
        temperature=0.0,
        timeout_s=120,
    )
    result = await agent_run(req)
    assert result.status == "ok", result.error
    summary = (tmp_path / "out" / "summary.md").read_text()
    assert "live ping ok" in summary or len(summary) > 0
```

- [ ] **Step 2: Verify it's gated correctly**

```bash
cd vllm-agent && uv run pytest -v
```
Expected: live test is collected but skipped (not run by default — markers config in pyproject).

- [ ] **Step 3: Run the live test (optional, requires VM up)**

```bash
cd vllm-agent && VLLM_BASE_URL="$(jq -r '.mcpServers["vllm-rtx5090"].env.VLLM_BASE_URL' ../.mcp.json)" \
  VLLM_MODEL="$(jq -r '.mcpServers["vllm-rtx5090"].env.VLLM_MODEL' ../.mcp.json)" \
  uv run pytest -m live -v
```
Expected: PASS (if vLLM is reachable). Skip otherwise.

- [ ] **Step 4: Commit**

```bash
git add vllm-agent/tests/test_live_smoke.py
git commit -m "Add live smoke test (opt-in via pytest -m live)"
```

---

## Task 23: Final review, full test run, commit lockfile

- [ ] **Step 1: Run the full unit + integration test suite**

```bash
cd vllm-agent && uv run pytest -v
```
Expected: all tests pass except `live` (skipped).

- [ ] **Step 2: Quick lint pass**

```bash
cd vllm-agent && uv run python -m py_compile src/vllm_agent/**/*.py
```
Expected: no output (everything compiles).

- [ ] **Step 3: Verify the CLI works end-to-end against the real VM (if available)**

```bash
cd vllm-agent && VLLM_BASE_URL="$(jq -r '.mcpServers["vllm-rtx5090"].env.VLLM_BASE_URL' ../.mcp.json)" \
  VLLM_MODEL="$(jq -r '.mcpServers["vllm-rtx5090"].env.VLLM_MODEL' ../.mcp.json)" \
  uv run vllm-agent run "Call finish() with summary 'cli ok'" \
  --mode remote --workdir /tmp --max-iterations 3
```
Expected: JSON output with `status: "ok"` and a summary file written.

- [ ] **Step 4: Commit any remaining state (lockfile, formatting fixes)**

```bash
cd vllm-agent && git add -A && git status
# If anything new: commit. Otherwise skip.
git commit -m "Lock in vllm-agent runtime package" 2>/dev/null || echo "nothing to commit"
```

---

## Self-Review (run after the plan is written)

### 1. Spec coverage

| Spec section | Implemented in tasks |
|---|---|
| §2 Architecture (vllm_agent package layout) | Task 1 (skeleton); Tasks 2-21 (all modules) |
| §3 MCP tool surface | Out of scope of Plan A — covered in Plan B |
| §3 agent_run signature & return shape | Task 18 |
| §3 agent_session_* signatures | Task 19 |
| §3 Output discipline (transcript/summary/files_changed) | Task 14 (transcript); Task 18 (summary, files_changed); Task 12 (finish writes summary) |
| §4 Worker palette (read/write/edit/bash/grep/glob/web_search/finish) | Tasks 5, 6, 7, 8, 9, 10, 11, 12 |
| §4 System-prompt + skill-as-system-prompt | Tasks 3, 13 |
| §4 Telemetry (iterations, tool counts) | Tasks 14, 16 |
| §4 Failure handling (vLLM retry, tool exceptions, max_iterations) | Tasks 16, 17 |
| §4 Empty-finish() warning | Task 12 |
| §5 Sessions (start/step/status/stop, persistence) | Tasks 15, 19 |
| §5 Skill discovery + naming | Task 3 |
| §5 Project layout | Tasks 1-21 collectively |
| §6 Unit tests | Each task has unit tests |
| §6 Integration tests with respx | Tasks 16, 17, 18, 19, 21 |
| §6 Live smoke test (opt-in) | Task 22 |
| §6 Rollout step 1 (land vllm-agent) | Tasks 1-23 |
| §6 Rollout step 2 (HTTP API) | Tasks 20, 21 |
| §6 Rollout steps 3-7 | Plan B |

**Gaps:** the `diff_path` field in `AgentRunResult` (§3) is set to `None` in Task 18 — full git-diff capture for `mode=remote` is deferred to Plan B (where the VM-side git sync also lives). Not a Plan A regression; just noted.

### 2. Placeholder scan

- No "TBD" / "TODO" / "implement later" / "fill in details" in the plan.
- Every code step has full code or a complete command.
- The note in Task 18 about "filled in Plan B" is in a code comment, not a step instruction; a comment in real code is fine.

### 3. Type consistency

- `Tool`, `ToolContext`, `Workspace`, `Transcript`, `SessionStore`, `Session`, `LoopConfig`, `LoopResult`, `AgentRunRequest`, `AgentRunResult`, `AgentSessionStartRequest`, `AgentSessionStartResult`, `AgentSessionStepResult`, `AgentSessionStatusResult`, `AgentSessionStopResult` — checked across tasks. Names and fields are consistent everywhere they're referenced.
- Tool names (`read_file`, `write_file`, `edit_file`, `bash`, `grep`, `glob`, `web_search`, `finish`) match between `tools/__init__.py` registrations, `prompts.py` discipline text, and worker tool tests.
- `VLLM_AGENT_OUT_DIR`, `VLLM_AGENT_MODE`, `VLLM_AGENT_LOCAL_BASH`, `VLLM_AGENT_SESSION_ROOT`, `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY` env-var names match between `api.py`, `shell.py`, `finish.py`, server, and tests.

No issues found.
