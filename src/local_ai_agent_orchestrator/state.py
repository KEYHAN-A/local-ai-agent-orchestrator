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

# Plan filename stems that must not map to a root-level project folder.
_RESERVED_PLAN_STEMS = frozenset(
    {
        "plans",
        "factory",
        "factory.example",
        "readme",
        ".lao",
    }
)


def plan_stem_reserved(stem: str) -> bool:
    s = stem.strip().lower()
    return s in _RESERVED_PLAN_STEMS or s.startswith(".lao")


class ReservedPlanStemError(ValueError):
    """Raised when a plan file stem conflicts with layout or config names."""


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

CREATE TABLE IF NOT EXISTS plan_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    tasks_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(plan_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS task_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES micro_tasks(id),
    source TEXT NOT NULL,
    severity TEXT NOT NULL,
    file_path TEXT,
    issue_class TEXT NOT NULL,
    message TEXT NOT NULL,
    fix_hint TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON micro_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_plan ON micro_tasks(plan_id);
CREATE INDEX IF NOT EXISTS idx_runlog_task ON run_log(task_id);
CREATE INDEX IF NOT EXISTS idx_plan_chunks_plan ON plan_chunks(plan_id);
CREATE INDEX IF NOT EXISTS idx_task_findings_task ON task_findings(task_id);
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
        self._conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript(SCHEMA)

    # ── Plan Management ──────────────────────────────────────────────

    def register_plan(self, filename: str, content: str) -> str:
        stem = Path(filename).stem
        if plan_stem_reserved(stem):
            raise ReservedPlanStemError(
                f"Plan filename {filename!r} resolves to reserved stem {stem!r}. "
                f"Rename the file so its stem is not one of: "
                f"{', '.join(sorted(_RESERVED_PLAN_STEMS))}."
            )
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

    def upsert_plan_chunk(self, plan_id: str, chunk_index: int, chunk_text: str):
        self._conn.execute(
            """INSERT INTO plan_chunks (plan_id, chunk_index, chunk_text, status)
               VALUES (?, ?, ?, 'pending')
               ON CONFLICT(plan_id, chunk_index)
               DO UPDATE SET chunk_text=excluded.chunk_text, updated_at=datetime('now')""",
            (plan_id, chunk_index, chunk_text),
        )

    def mark_plan_chunk_done(
        self, plan_id: str, chunk_index: int, tasks: list[dict]
    ):
        self._conn.execute(
            """UPDATE plan_chunks
               SET status='completed', tasks_json=?, error=NULL, updated_at=datetime('now')
               WHERE plan_id=? AND chunk_index=?""",
            (json.dumps(tasks), plan_id, chunk_index),
        )

    def mark_plan_chunk_failed(self, plan_id: str, chunk_index: int, error: str):
        self._conn.execute(
            """UPDATE plan_chunks
               SET status='failed', error=?, updated_at=datetime('now')
               WHERE plan_id=? AND chunk_index=?""",
            (error, plan_id, chunk_index),
        )

    def get_plan_chunks(self, plan_id: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT chunk_index, chunk_text, status, tasks_json, error
               FROM plan_chunks WHERE plan_id=? ORDER BY chunk_index ASC""",
            (plan_id,),
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "chunk_index": r["chunk_index"],
                    "chunk_text": r["chunk_text"],
                    "status": r["status"],
                    "tasks": json.loads(r["tasks_json"]) if r["tasks_json"] else None,
                    "error": r["error"],
                }
            )
        return out

    def workspace_for_plan(self, plan_id: str) -> Path:
        """
        Per-plan workspace: <config_dir>/<plan_stem>/
        where plan_stem is the plan .md filename without extension.
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
        root = (s.config_dir / stem).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    # ── Task CRUD ────────────────────────────────────────────────────

    def add_tasks(self, plan_id: str, tasks: list[dict]):
        """Bulk-insert micro-tasks from the architect's output."""
        title_set = {str(t.get("title", "")).strip() for t in tasks}
        self._conn.execute("BEGIN")
        try:
            for i, t in enumerate(tasks):
                deps = [str(d).strip() for d in t.get("dependencies", []) if str(d).strip()]
                cleaned_deps = [d for d in deps if d in title_set]
                if len(cleaned_deps) != len(deps):
                    dropped = set(deps) - set(cleaned_deps)
                    log.warning(
                        "[State] Task %r had unknown dependencies dropped: %s",
                        t.get("title", "Untitled"),
                        ", ".join(sorted(dropped)),
                    )
                self._conn.execute(
                    """INSERT INTO micro_tasks
                       (plan_id, title, description, file_paths, dependencies, priority)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        plan_id,
                        t["title"],
                        t["description"],
                        json.dumps(t.get("file_paths", [])),
                        json.dumps(cleaned_deps),
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
        rows = self._conn.execute(
            """SELECT * FROM micro_tasks
               WHERE status = 'pending'
               ORDER BY priority ASC, id ASC"""
        ).fetchall()
        if not rows:
            return None
        completed_cache: dict[str, set[str]] = {}
        for row in rows:
            plan_id = row["plan_id"]
            if plan_id not in completed_cache:
                completed_cache[plan_id] = {
                    r["title"]
                    for r in self._conn.execute(
                        "SELECT title FROM micro_tasks WHERE plan_id=? AND status='completed'",
                        (plan_id,),
                    ).fetchall()
                }
            deps = json.loads(row["dependencies"])
            if all(d in completed_cache[plan_id] for d in deps):
                return self._row_to_task(row)
        return None

    def next_pending_batch(self, limit: int = 4) -> list[MicroTask]:
        """Get a batch of runnable pending tasks with dependency checks."""
        out: list[MicroTask] = []
        while len(out) < max(1, limit):
            task = self.next_pending()
            if not task:
                break
            self.mark_coding(task.id)
            out.append(task)
        for t in out:
            self._update_status(t.id, "pending")
        return out

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

    def get_plans(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, filename, status, created_at FROM plans ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

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

    def get_efficiency_metrics(self) -> dict:
        rows = self._conn.execute(
            "SELECT model_key FROM run_log WHERE model_key IS NOT NULL ORDER BY id ASC"
        ).fetchall()
        switches = 0
        prev = None
        for r in rows:
            cur = r["model_key"]
            if prev is not None and cur != prev:
                switches += 1
            prev = cur
        return {
            "model_switches": switches,
            "run_events": len(rows),
        }

    def add_finding(
        self,
        task_id: int,
        source: str,
        severity: str,
        issue_class: str,
        message: str,
        file_path: Optional[str] = None,
        fix_hint: Optional[str] = None,
    ):
        self._conn.execute(
            """INSERT INTO task_findings
               (task_id, source, severity, file_path, issue_class, message, fix_hint)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, source, severity, file_path, issue_class, message, fix_hint),
        )

    def clear_findings(self, task_id: int):
        self._conn.execute("DELETE FROM task_findings WHERE task_id = ?", (task_id,))

    def get_findings(self, task_id: int) -> list[dict]:
        rows = self._conn.execute(
            """SELECT source, severity, file_path, issue_class, message, fix_hint
               FROM task_findings WHERE task_id = ? ORDER BY id ASC""",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

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
