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
