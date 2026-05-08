"""Workspace = a resolved working directory for an agent run.

Path-confinement here is *advisory* — the bash tool can break it. The
`is_inside` helper is used by FS tools to flag (not block) cross-workspace
edits in the transcript.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKSPACE_ROOT = Path("~/.cache/vllm-agent/workspaces").expanduser()


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def resolve(cls, workdir: str | None) -> "Workspace":
        if workdir is None:
            # Mint an isolated per-run workspace. Never fall back to Path.cwd():
            # under `vllm-agent serve` cwd is the bind-mounted source repo, and
            # writing there pollutes /app on the host.
            base = Path(os.environ.get(
                "VLLM_AGENT_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT)))
            root = base / f"ws-{uuid.uuid4().hex[:12]}"
        else:
            root = Path(workdir).expanduser().resolve()
        # Auto-create (matches out_dir behavior in api.py); refusing to run
        # because of a missing dir surfaced as a 502 to clients.
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise FileNotFoundError(f"workdir is not a directory: {root}")
        return cls(root=root.resolve())

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
