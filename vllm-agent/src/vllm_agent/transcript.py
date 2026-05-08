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
