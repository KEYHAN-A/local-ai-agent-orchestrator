# SPDX-License-Identifier: GPL-3.0-or-later
"""
Lightweight persistent registry of known LAO workspaces.

Stores project metadata in ``~/.lao/projects.json`` so that ``lao`` can
discover, list, and switch between projects even when invoked from a
parent directory that is not itself a LAO workspace.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_REGISTRY_PATH = Path.home() / ".lao" / "projects.json"

_LAO_CONFIG_NAMES = ("factory.yaml", "factory.yml")


@dataclass
class ProjectEntry:
    path: str
    name: str
    last_used: str = ""
    has_config: bool = False
    plans_count: int = 0
    pending_tasks: int = 0
    failed_tasks: int = 0
    tags: list[str] = field(default_factory=list)

    def touch(self) -> None:
        self.last_used = datetime.now(timezone.utc).isoformat()


class ProjectRegistry:
    """Manage a persistent list of known LAO workspaces."""

    def __init__(self, registry_path: Optional[Path] = None) -> None:
        self._path = registry_path or _DEFAULT_REGISTRY_PATH
        self._entries: list[ProjectEntry] = []
        self._load()

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            self._entries = []
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._entries = [ProjectEntry(**e) for e in raw]
        except Exception as exc:
            log.warning("Failed to load project registry: %s", exc)
            self._entries = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(e) for e in self._entries]
        self._path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Public API ───────────────────────────────────────────────────

    def scan(self, root: Path, max_depth: int = 3) -> list[ProjectEntry]:
        """Walk *root* up to *max_depth* levels and register any LAO projects found."""
        found: list[ProjectEntry] = []
        root = root.resolve()
        self._scan_recursive(root, max_depth, 0, found)
        self._save()
        return found

    def add(self, path: Path, tags: Optional[list[str]] = None) -> ProjectEntry:
        """Register a project (or update it if already known)."""
        abs_path = str(path.resolve())
        for entry in self._entries:
            if entry.path == abs_path:
                entry.touch()
                entry = self.refresh(entry)
                self._save()
                return entry
        entry = ProjectEntry(
            path=abs_path,
            name=path.resolve().name,
            tags=tags or [],
        )
        entry.touch()
        entry = self.refresh(entry)
        self._entries.append(entry)
        self._save()
        return entry

    def remove(self, path_or_name: str) -> bool:
        before = len(self._entries)
        self._entries = [
            e for e in self._entries
            if e.path != path_or_name and e.name != path_or_name
        ]
        removed = len(self._entries) < before
        if removed:
            self._save()
        return removed

    def list_all(self) -> list[ProjectEntry]:
        return list(self._entries)

    def get(self, name_or_path: str) -> Optional[ProjectEntry]:
        resolved = None
        try:
            resolved = str(Path(name_or_path).resolve())
        except Exception:
            pass
        for e in self._entries:
            if e.path == name_or_path or e.name == name_or_path:
                return e
            if resolved and e.path == resolved:
                return e
        return None

    def needs_action(self) -> list[ProjectEntry]:
        """Return projects with pending/failed work, sorted by urgency."""
        scored: list[tuple[int, ProjectEntry]] = []
        for e in self._entries:
            e = self.refresh(e)
            score = 0
            if e.pending_tasks or e.failed_tasks:
                score += 40
            if e.has_config and e.plans_count and not e.pending_tasks and not e.failed_tasks:
                score += 20
            if e.plans_count and not e.has_config:
                score += 30
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        self._save()
        return [e for _, e in scored]

    def refresh(self, entry: ProjectEntry) -> ProjectEntry:
        """Re-read live filesystem/DB data for a project entry."""
        p = Path(entry.path)
        entry.has_config = any((p / n).exists() for n in _LAO_CONFIG_NAMES)

        plans_dir = p / "plans"
        if plans_dir.is_dir():
            entry.plans_count = len(list(plans_dir.glob("*.md")))
        else:
            entry.plans_count = 0

        entry.pending_tasks = 0
        entry.failed_tasks = 0
        db_path = p / ".lao" / "state.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path), timeout=2)
                conn.execute("PRAGMA journal_mode=WAL")
                row = conn.execute(
                    "SELECT "
                    "  SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),"
                    "  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END)"
                    " FROM tasks"
                ).fetchone()
                if row:
                    entry.pending_tasks = row[0] or 0
                    entry.failed_tasks = row[1] or 0
                conn.close()
            except Exception:
                pass

        return entry

    # ── Internal ─────────────────────────────────────────────────────

    def _scan_recursive(
        self,
        directory: Path,
        max_depth: int,
        depth: int,
        found: list[ProjectEntry],
    ) -> None:
        if depth > max_depth:
            return
        if self._is_lao_project(directory):
            entry = self.add(directory)
            found.append(entry)
            return  # Don't recurse into a found project
        try:
            children = sorted(directory.iterdir())
        except PermissionError:
            return
        for child in children:
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if child.name in ("node_modules", "__pycache__", ".git", ".venv", "venv"):
                continue
            self._scan_recursive(child, max_depth, depth + 1, found)

    @staticmethod
    def _is_lao_project(directory: Path) -> bool:
        if any((directory / n).exists() for n in _LAO_CONFIG_NAMES):
            return True
        plans_dir = directory / "plans"
        if plans_dir.is_dir() and any(plans_dir.glob("*.md")):
            return True
        if (directory / ".lao" / "state.db").exists():
            return True
        return False
