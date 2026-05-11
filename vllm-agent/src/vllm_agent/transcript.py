"""Transcript: append-only JSONL recorder for an agent run."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MIN_REDACT_LEN = 8


@dataclass
class Transcript:
    path: Path
    redact_values: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        # Only redact values long enough to be unique-ish (avoids mangling
        # unrelated short substrings).
        self._redact = [v for v in self.redact_values if v and len(v) >= _MIN_REDACT_LEN]

    def _scrub(self, line: str) -> str:
        for v in self._redact:
            if v in line:
                line = line.replace(v, "[REDACTED]")
        return line

    def append(self, record: dict[str, Any]) -> None:
        record = {"ts": time.time(), **record}
        line = json.dumps(record, ensure_ascii=False)
        with self.path.open("a") as f:
            f.write(self._scrub(line) + "\n")

    def record_message(self, role: str, content: Any) -> None:
        self.append({"kind": "message", "role": role, "content": content})

    def record_tool_call(self, tool: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        self.append({"kind": "tool_call", "tool": tool, "args": args, "result": result})
