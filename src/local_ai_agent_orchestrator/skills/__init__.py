# SPDX-License-Identifier: GPL-3.0-or-later
"""
Skills system.

A *skill* is a named, reusable workflow defined by a Markdown file with a YAML
front matter block. Skills wrap a system-prompt addendum and an optional tool
whitelist; activating one stamps its addendum onto the next system prompt.

Front-matter schema (all keys optional except ``name``)::

    ---
    name: verify
    description: Run mechanical checks before declaring done.
    tools: [file_read, list_dir, shell_exec]
    examples:
      - Re-read the target file and confirm it parses.
    ---
    <body becomes the addendum>

Bundled skills live under ``skills/bundled/``; user skills live under any
directory listed in ``factory.yaml: skills.dirs`` (default ``.lao/skills``).
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml

from local_ai_agent_orchestrator.settings import get_settings

log = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    body: str = ""
    source_path: Optional[Path] = None

    def addendum(self) -> str:
        parts: list[str] = [f"Skill: {self.name}"]
        if self.description:
            parts.append(self.description)
        if self.body:
            parts.append(self.body.strip())
        if self.tools:
            parts.append("Allowed tools: " + ", ".join(self.tools))
        if self.examples:
            parts.append("Examples:\n- " + "\n- ".join(self.examples))
        return "\n\n".join(parts)


_REGISTRY: dict[str, Skill] = {}
_ACTIVE: ContextVar[Optional[str]] = ContextVar("lao_active_skill", default=None)


def _bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "bundled"


def _user_dirs() -> Iterable[Path]:
    try:
        s = get_settings()
    except RuntimeError:
        return ()
    paths: list[Path] = []
    for d in s.skills_dirs or []:
        try:
            paths.append(Path(d).expanduser().resolve())
        except Exception:
            continue
    default_user = (s.config_dir / ".lao" / "skills").resolve()
    if default_user not in paths:
        paths.append(default_user)
    return paths


def _parse_skill_file(path: Path) -> Optional[Skill]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    front: dict = {}
    body = text
    if text.startswith("---"):
        try:
            _, fm, rest = text.split("---", 2)
            front = yaml.safe_load(fm) or {}
            body = rest.strip()
        except Exception:
            front = {}
            body = text
    name = str(front.get("name") or path.stem).strip()
    if not name:
        return None
    return Skill(
        name=name,
        description=str(front.get("description") or "").strip(),
        tools=[str(t) for t in (front.get("tools") or []) if str(t).strip()],
        examples=[str(e) for e in (front.get("examples") or []) if str(e).strip()],
        body=body,
        source_path=path,
    )


def load_skills(force: bool = False) -> dict[str, Skill]:
    """Load (or reload) bundled + user skills into the registry."""
    if _REGISTRY and not force:
        return _REGISTRY
    _REGISTRY.clear()
    enabled = True
    try:
        enabled = bool(get_settings().skills_enabled)
    except RuntimeError:
        enabled = True
    if not enabled:
        return _REGISTRY
    for d in [_bundled_dir(), *_user_dirs()]:
        if not d.exists() or not d.is_dir():
            continue
        for fp in sorted(d.glob("*.md")):
            sk = _parse_skill_file(fp)
            if sk is not None:
                _REGISTRY[sk.name] = sk
    log.debug(f"[Skills] loaded {len(_REGISTRY)} skill(s)")
    return _REGISTRY


def list_skills() -> list[Skill]:
    return list(load_skills().values())


def get_skill(name: str) -> Optional[Skill]:
    return load_skills().get(name)


def activate(name: str) -> Optional[Skill]:
    sk = get_skill(name)
    if sk is None:
        return None
    _ACTIVE.set(name)
    return sk


def deactivate() -> None:
    _ACTIVE.set(None)


def active_skill() -> Optional[Skill]:
    name = _ACTIVE.get()
    if not name:
        return None
    return get_skill(name)


def active_addendum() -> str:
    sk = active_skill()
    return sk.addendum() if sk else ""
