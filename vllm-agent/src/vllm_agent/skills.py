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
