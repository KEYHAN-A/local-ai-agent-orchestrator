# SPDX-License-Identifier: GPL-3.0-or-later
"""
Hierarchical memory: per-project ``LAO_MEMORY.md`` + user-global
``~/.lao/MEMORY.md``.

Inspired by Claude Code's ``memdir/`` and ``CLAUDE.md`` injection: keep a
small, append-only Markdown ledger of durable facts (architectural decisions,
build/lint commands, naming conventions detected by the analyst). The contents
are injected as a system-prompt prelude on every phase that opts in.

The companion ``services/extract_memories.py`` runs after a successful reviewer
approval and dedupe-appends new facts to both layers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.settings import get_settings

log = logging.getLogger(__name__)


_PROJECT_HEADER = "# LAO Project Memory\n\n"
_USER_HEADER = "# LAO User Memory\n\n"


def _project_memory_path() -> Optional[Path]:
    try:
        s = get_settings()
    except RuntimeError:
        return None
    if not s.memory_enabled:
        return None
    return (s.config_dir / s.memory_project_filename).resolve()


def _user_memory_path() -> Optional[Path]:
    try:
        s = get_settings()
    except RuntimeError:
        return None
    if not s.memory_enabled:
        return None
    if s.memory_user_path:
        return Path(s.memory_user_path).expanduser().resolve()
    return (Path.home() / ".lao" / "MEMORY.md").resolve()


def _read(path: Optional[Path]) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as e:
        log.debug(f"[Memory] read {path}: {e}")
        return ""


def read_memory_block() -> str:
    """Return the system-prompt prelude (empty string when disabled)."""
    project = _read(_project_memory_path())
    user = _read(_user_memory_path())
    parts: list[str] = []
    if project:
        parts.append("=== Project memory (LAO_MEMORY.md) ===\n" + project)
    if user:
        parts.append("=== User memory (~/.lao/MEMORY.md) ===\n" + user)
    if not parts:
        return ""
    return "\n\n".join(parts)


def append_fact(fact: str, *, scope: str = "project", source: Optional[str] = None) -> bool:
    """Append a fact line to the chosen memory layer; idempotent across runs."""
    fact = (fact or "").strip()
    if not fact:
        return False
    path = _project_memory_path() if scope == "project" else _user_memory_path()
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    header = _PROJECT_HEADER if scope == "project" else _USER_HEADER
    existing = path.read_text(encoding="utf-8") if path.exists() else header
    line = f"- {fact}"
    if source:
        line += f"  _(source: {source})_"
    if line in existing:
        return False
    if not existing.endswith("\n"):
        existing += "\n"
    existing += line + "\n"
    path.write_text(existing, encoding="utf-8")
    return True


def forget_fact(fact_substring: str, *, scope: str = "project") -> int:
    """Remove every line containing *fact_substring*; returns the count removed."""
    path = _project_memory_path() if scope == "project" else _user_memory_path()
    if path is None or not path.exists():
        return 0
    needle = (fact_substring or "").strip().lower()
    if not needle:
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if needle not in ln.lower()]
    removed = len(lines) - len(kept)
    if removed:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed
