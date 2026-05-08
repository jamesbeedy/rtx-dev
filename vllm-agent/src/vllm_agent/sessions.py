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
    skill_content: str | None = None
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

    def session_dir(self, session_id: str) -> Path:
        """Public alias for the session's on-disk directory."""
        return self._dir(session_id)

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
