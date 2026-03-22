# SPDX-License-Identifier: GPL-3.0-or-later
"""
Persistent task queue backed by SQLite with WAL mode for crash safety.
Tracks micro-tasks through their lifecycle: pending -> coding -> coded -> review -> completed.
Also maintains a full audit log of every LLM call.
"""

import sqlite3
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from local_ai_agent_orchestrator.settings import get_settings

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'decomposing',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS micro_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    file_paths TEXT NOT NULL DEFAULT '[]',
    dependencies TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    coder_output TEXT,
    reviewer_feedback TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES micro_tasks(id),
    phase TEXT NOT NULL,
    model_key TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    duration_seconds REAL,
    success INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON micro_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_plan ON micro_tasks(plan_id);
CREATE INDEX IF NOT EXISTS idx_runlog_task ON run_log(task_id);
"""


@dataclass
class MicroTask:
    id: int
    plan_id: str
    title: str
    description: str
    file_paths: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    status: str = "pending"
    coder_output: Optional[str] = None
    reviewer_feedback: Optional[str] = None
    attempt: int = 0
    max_attempts: int = 3
    priority: int = 0


class TaskQueue:
    """SQLite-backed persistent task queue with crash recovery."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_settings().db_path
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript(SCHEMA)

    # ── Plan Management ──────────────────────────────────────────────

    def register_plan(self, filename: str, content: str) -> str:
        plan_id = hashlib.sha256(content.encode()).hexdigest()[:16]
        try:
            self._conn.execute(
                "INSERT INTO plans (id, filename) VALUES (?, ?)",
                (plan_id, filename),
            )
            log.info(f"[State] Registered plan: {filename} -> {plan_id}")
        except sqlite3.IntegrityError:
            log.info(f"[State] Plan already registered: {plan_id}")
        return plan_id

    def is_plan_registered(self, content: str) -> bool:
        plan_id = hashlib.sha256(content.encode()).hexdigest()[:16]
        row = self._conn.execute(
            "SELECT id FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return row is not None

    def mark_plan_active(self, plan_id: str):
        self._conn.execute(
            "UPDATE plans SET status = 'active' WHERE id = ?", (plan_id,)
        )

    def mark_plan_completed(self, plan_id: str):
        self._conn.execute(
            "UPDATE plans SET status = 'completed' WHERE id = ?", (plan_id,)
        )

    def workspace_for_plan(self, plan_id: str) -> Path:
        """
        Per-plan workspace: <config_dir>/.lao/workspaces/<plan_stem>/
        where plan_stem is the .md filename without extension.
        """
        row = self._conn.execute(
            "SELECT filename FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        s = get_settings()
        if not row:
            p = s.workspace_root
            p.mkdir(parents=True, exist_ok=True)
            return p.resolve()
        stem = Path(row[0]).stem
        root = (s.config_dir / ".lao" / "workspaces" / stem).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    # ── Task CRUD ────────────────────────────────────────────────────

    def add_tasks(self, plan_id: str, tasks: list[dict]):
        """Bulk-insert micro-tasks from the architect's output."""
        self._conn.execute("BEGIN")
        try:
            for i, t in enumerate(tasks):
                self._conn.execute(
                    """INSERT INTO micro_tasks
                       (plan_id, title, description, file_paths, dependencies, priority)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        plan_id,
                        t["title"],
                        t["description"],
                        json.dumps(t.get("file_paths", [])),
                        json.dumps(t.get("dependencies", [])),
                        i,
                    ),
                )
            self._conn.execute("COMMIT")
            log.info(f"[State] Added {len(tasks)} tasks for plan {plan_id}")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def next_pending(self) -> Optional[MicroTask]:
        """Get the next task ready for coding (all dependencies satisfied)."""
        row = self._conn.execute(
            """SELECT * FROM micro_tasks
               WHERE status = 'pending'
               ORDER BY priority ASC, id ASC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def next_coded(self) -> Optional[MicroTask]:
        """Get the next task ready for review."""
        row = self._conn.execute(
            """SELECT * FROM micro_tasks
               WHERE status = 'coded'
               ORDER BY priority ASC, id ASC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def get_task(self, task_id: int) -> Optional[MicroTask]:
        row = self._conn.execute(
            "SELECT * FROM micro_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    # ── Status Transitions ───────────────────────────────────────────

    def mark_coding(self, task_id: int):
        self._update_status(task_id, "coding")

    def mark_coded(self, task_id: int, coder_output: str):
        self._conn.execute(
            """UPDATE micro_tasks
               SET status='coded', coder_output=?, updated_at=datetime('now')
               WHERE id=?""",
            (coder_output, task_id),
        )

    def mark_review(self, task_id: int):
        self._update_status(task_id, "review")

    def mark_completed(self, task_id: int):
        self._update_status(task_id, "completed")

    def mark_rework(self, task_id: int, feedback: str):
        self._conn.execute(
            """UPDATE micro_tasks
               SET status='pending', reviewer_feedback=?, attempt=attempt+1,
                   updated_at=datetime('now')
               WHERE id=?""",
            (feedback, task_id),
        )

    def mark_failed(self, task_id: int, error: str):
        self._conn.execute(
            """UPDATE micro_tasks
               SET status='failed', reviewer_feedback=?, updated_at=datetime('now')
               WHERE id=?""",
            (error, task_id),
        )

    def _update_status(self, task_id: int, status: str):
        self._conn.execute(
            "UPDATE micro_tasks SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, task_id),
        )

    # ── Crash Recovery ───────────────────────────────────────────────

    def recover_interrupted(self) -> int:
        """Reset tasks stuck in transient states from a previous crash."""
        count = 0
        # Tasks stuck in 'coding' -> reset to 'pending'
        cur = self._conn.execute(
            "UPDATE micro_tasks SET status='pending' WHERE status='coding'"
        )
        count += cur.rowcount
        # Tasks stuck in 'review' -> reset to 'coded'
        cur = self._conn.execute(
            "UPDATE micro_tasks SET status='coded' WHERE status='review'"
        )
        count += cur.rowcount
        if count:
            log.warning(f"[State] Recovered {count} interrupted tasks")
        return count

    # ── Queries ──────────────────────────────────────────────────────

    def has_pending_work(self) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM micro_tasks WHERE status IN ('pending', 'coded', 'rework')"
        ).fetchone()
        return row["c"] > 0

    def has_any_tasks(self) -> bool:
        row = self._conn.execute("SELECT COUNT(*) as c FROM micro_tasks").fetchone()
        return row["c"] > 0

    def get_stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as c FROM micro_tasks GROUP BY status"
        ).fetchall()
        return {r["status"]: r["c"] for r in rows}

    def get_plan_tasks(self, plan_id: str) -> list[MicroTask]:
        rows = self._conn.execute(
            "SELECT * FROM micro_tasks WHERE plan_id = ? ORDER BY priority",
            (plan_id,),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    # ── Run Logging ──────────────────────────────────────────────────

    def log_run(
        self,
        task_id: Optional[int],
        phase: str,
        model_key: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_seconds: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ):
        self._conn.execute(
            """INSERT INTO run_log
               (task_id, phase, model_key, prompt_tokens, completion_tokens,
                duration_seconds, success, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, phase, model_key, prompt_tokens, completion_tokens,
             duration_seconds, int(success), error),
        )

    def get_total_tokens(self) -> dict:
        row = self._conn.execute(
            """SELECT COALESCE(SUM(prompt_tokens), 0) as p,
                      COALESCE(SUM(completion_tokens), 0) as c
               FROM run_log"""
        ).fetchone()
        return {"prompt_tokens": row["p"], "completion_tokens": row["c"]}

    # ── Helpers ──────────────────────────────────────────────────────

    def _row_to_task(self, row: sqlite3.Row) -> MicroTask:
        return MicroTask(
            id=row["id"],
            plan_id=row["plan_id"],
            title=row["title"],
            description=row["description"],
            file_paths=json.loads(row["file_paths"]),
            dependencies=json.loads(row["dependencies"]),
            status=row["status"],
            coder_output=row["coder_output"],
            reviewer_feedback=row["reviewer_feedback"],
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
            priority=row["priority"],
        )

    def close(self):
        self._conn.close()
