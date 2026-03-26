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
    preflight_json TEXT,
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
    phase_name TEXT,
    deliverable_ids TEXT NOT NULL DEFAULT '[]',
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

CREATE TABLE IF NOT EXISTS plan_phases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    phase_name TEXT NOT NULL,
    phase_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    UNIQUE(plan_id, phase_name)
);

CREATE TABLE IF NOT EXISTS plan_deliverables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    deliverable_id TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'not_started',
    status_reason TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(plan_id, deliverable_id)
);

CREATE TABLE IF NOT EXISTS task_validation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES micro_tasks(id),
    kind TEXT NOT NULL,
    command TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    return_code INTEGER,
    success INTEGER NOT NULL,
    output TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON micro_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_plan ON micro_tasks(plan_id);
CREATE INDEX IF NOT EXISTS idx_runlog_task ON run_log(task_id);
CREATE INDEX IF NOT EXISTS idx_plan_chunks_plan ON plan_chunks(plan_id);
CREATE INDEX IF NOT EXISTS idx_task_findings_task ON task_findings(task_id);
CREATE INDEX IF NOT EXISTS idx_plan_phases_plan ON plan_phases(plan_id);
CREATE INDEX IF NOT EXISTS idx_deliverables_plan ON plan_deliverables(plan_id);
CREATE INDEX IF NOT EXISTS idx_task_validation_runs_task ON task_validation_runs(task_id);
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
    phase_name: Optional[str] = None
    deliverable_ids: list[str] = field(default_factory=list)
    attempt: int = 0
    max_attempts: int = 3
    priority: int = 0


class TaskQueue:
    """SQLite-backed persistent task queue with crash recovery."""

    _ALLOWED_DELIVERABLE_STATUSES = {
        "not_started",
        "in_progress",
        "validated",
        "deferred",
        "blocked",
        "failed",
        "partial",
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_settings().db_path
        self._conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript(SCHEMA)
        self._run_migrations()

    def _run_migrations(self):
        cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(plans)").fetchall()
        }
        if "preflight_json" not in cols:
            self._conn.execute("ALTER TABLE plans ADD COLUMN preflight_json TEXT")
        task_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(micro_tasks)").fetchall()
        }
        if "phase_name" not in task_cols:
            self._conn.execute("ALTER TABLE micro_tasks ADD COLUMN phase_name TEXT")
        if "deliverable_ids" not in task_cols:
            self._conn.execute(
                "ALTER TABLE micro_tasks ADD COLUMN deliverable_ids TEXT NOT NULL DEFAULT '[]'"
            )
        validation_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(task_validation_runs)").fetchall()
        }
        if "status" not in validation_cols:
            self._conn.execute(
                "ALTER TABLE task_validation_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
            )
        if "return_code" not in validation_cols:
            self._conn.execute("ALTER TABLE task_validation_runs ADD COLUMN return_code INTEGER")
        if "started_at" not in validation_cols:
            self._conn.execute("ALTER TABLE task_validation_runs ADD COLUMN started_at TEXT")
        if "finished_at" not in validation_cols:
            self._conn.execute("ALTER TABLE task_validation_runs ADD COLUMN finished_at TEXT")
        deliverable_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(plan_deliverables)").fetchall()
        }
        if "status_reason" not in deliverable_cols:
            self._conn.execute("ALTER TABLE plan_deliverables ADD COLUMN status_reason TEXT")
        if "updated_at" not in deliverable_cols:
            self._conn.execute(
                "ALTER TABLE plan_deliverables ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
            )

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

    def set_plan_preflight(self, plan_id: str, payload: dict):
        self._conn.execute(
            "UPDATE plans SET preflight_json=? WHERE id=?",
            (json.dumps(payload), plan_id),
        )

    def get_plan_preflight(self, plan_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT preflight_json FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not row or not row["preflight_json"]:
            return None
        try:
            return json.loads(row["preflight_json"])
        except Exception:
            return None

    def is_plan_terminal(self, plan_id: str) -> bool:
        """
        A plan is terminal when all its tasks are either completed or failed.
        """
        row = self._conn.execute(
            """SELECT COUNT(*) as c FROM micro_tasks
               WHERE plan_id = ? AND status NOT IN ('completed', 'failed')""",
            (plan_id,),
        ).fetchone()
        return bool(row) and int(row["c"]) == 0

    def is_plan_closure_satisfied(
        self,
        plan_id: str,
        strict_adherence: bool = False,
        allowed_statuses: set[str] | None = None,
    ) -> bool:
        if not self.is_plan_terminal(plan_id):
            return False
        if not strict_adherence:
            return True
        allowed = {s.strip().lower() for s in (allowed_statuses or {"validated"}) if s.strip()}
        if not allowed:
            allowed = {"validated"}
        row = self._conn.execute(
            """SELECT COUNT(*) as c FROM plan_deliverables
               WHERE plan_id = ? AND lower(status) NOT IN ({})""".format(
                ",".join("?" for _ in sorted(allowed))
            ),
            (plan_id, *sorted(allowed)),
        ).fetchone()
        return bool(row) and int(row["c"]) == 0

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
                       (plan_id, title, description, file_paths, dependencies, priority, phase_name, deliverable_ids)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        plan_id,
                        t["title"],
                        t["description"],
                        json.dumps(t.get("file_paths", [])),
                        json.dumps(cleaned_deps),
                        i,
                        (t.get("phase") or "").strip() or None,
                        json.dumps(t.get("deliverable_ids", [])),
                    ),
                )
            self._conn.execute("COMMIT")
            log.info(f"[State] Added {len(tasks)} tasks for plan {plan_id}")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def next_pending(self, phase_name: str | None = None) -> Optional[MicroTask]:
        """Get the next task ready for coding (all dependencies satisfied)."""
        while True:
            if phase_name:
                rows = self._conn.execute(
                    """SELECT * FROM micro_tasks
                       WHERE status = 'pending' AND phase_name = ?
                       ORDER BY priority ASC, id ASC""",
                    (phase_name,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM micro_tasks
                       WHERE status = 'pending'
                       ORDER BY priority ASC, id ASC"""
                ).fetchall()
            if not rows:
                return None

            completed_cache: dict[str, set[str]] = {}
            failed_cache: dict[str, set[str]] = {}
            progressed = False

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
                if plan_id not in failed_cache:
                    failed_cache[plan_id] = {
                        r["title"]
                        for r in self._conn.execute(
                            "SELECT title FROM micro_tasks WHERE plan_id=? AND status='failed'",
                            (plan_id,),
                        ).fetchall()
                    }

                deps = json.loads(row["dependencies"])
                failed_deps = [d for d in deps if d in failed_cache[plan_id]]
                if failed_deps:
                    self.mark_failed(
                        row["id"],
                        "Blocked by failed dependencies: " + ", ".join(sorted(failed_deps)),
                    )
                    log.warning(
                        "[State] Task #%s auto-failed due to failed dependencies: %s",
                        row["id"],
                        ", ".join(sorted(failed_deps)),
                    )
                    progressed = True
                    continue

                if all(d in completed_cache[plan_id] for d in deps):
                    return self._row_to_task(row)

            if not progressed:
                return None

    def next_pending_batch(self, limit: int = 4, phase_name: str | None = None) -> list[MicroTask]:
        """Get a batch of runnable pending tasks with dependency checks."""
        out: list[MicroTask] = []
        while len(out) < max(1, limit):
            task = self.next_pending(phase_name=phase_name)
            if not task:
                break
            self.mark_coding(task.id)
            out.append(task)
        for t in out:
            self._update_status(t.id, "pending")
        return out

    def next_coded(self, phase_name: str | None = None) -> Optional[MicroTask]:
        """Get the next task ready for review."""
        if phase_name:
            row = self._conn.execute(
                """SELECT * FROM micro_tasks
                   WHERE status = 'coded' AND phase_name = ?
                   ORDER BY priority ASC, id ASC
                   LIMIT 1""",
                (phase_name,),
            ).fetchone()
        else:
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

    def reset_failed_tasks(self, plan_id: Optional[str] = None) -> int:
        if plan_id:
            cur = self._conn.execute(
                """UPDATE micro_tasks
                   SET status='pending', attempt=0, reviewer_feedback=NULL, updated_at=datetime('now')
                   WHERE status='failed' AND plan_id=?""",
                (plan_id,),
            )
        else:
            cur = self._conn.execute(
                """UPDATE micro_tasks
                   SET status='pending', attempt=0, reviewer_feedback=NULL, updated_at=datetime('now')
                   WHERE status='failed'"""
            )
        return int(cur.rowcount or 0)

    def get_plans(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, filename, status, preflight_json, created_at FROM plans ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_plan_phases(self, plan_id: str, phases: list[str]):
        self._conn.execute("DELETE FROM plan_phases WHERE plan_id = ?", (plan_id,))
        for idx, phase_name in enumerate(phases):
            self._conn.execute(
                """INSERT INTO plan_phases (plan_id, phase_name, phase_index, status)
                   VALUES (?, ?, ?, 'pending')""",
                (plan_id, phase_name, idx),
            )

    def get_plan_phases(self, plan_id: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT phase_name, phase_index, status FROM plan_phases
               WHERE plan_id=? ORDER BY phase_index ASC""",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_deliverables(self, plan_id: str, items: list[dict]):
        for it in items:
            did = str(it.get("id", "")).strip()
            if not did:
                continue
            self._conn.execute(
                """INSERT INTO plan_deliverables (plan_id, deliverable_id, description, status)
                   VALUES (?, ?, ?, 'not_started')
                   ON CONFLICT(plan_id, deliverable_id)
                   DO UPDATE SET description=excluded.description, updated_at=datetime('now')""",
                (plan_id, did, str(it.get("description", "")).strip() or None),
            )

    def set_deliverable_status(
        self,
        plan_id: str,
        deliverable_id: str,
        status: str,
        reason: str | None = None,
    ):
        normalized = (status or "").strip().lower()
        if normalized not in self._ALLOWED_DELIVERABLE_STATUSES:
            raise ValueError(
                f"Invalid deliverable status {status!r}. "
                f"Allowed: {', '.join(sorted(self._ALLOWED_DELIVERABLE_STATUSES))}"
            )
        # Non-validated terminal risk states should carry explicit operator context.
        if normalized in {"deferred", "blocked", "failed", "partial"} and not (reason or "").strip():
            raise ValueError(f"Deliverable status '{normalized}' requires a non-empty reason.")
        self._conn.execute(
            """UPDATE plan_deliverables SET status=?, status_reason=?, updated_at=datetime('now')
               WHERE plan_id=? AND deliverable_id=?""",
            (normalized, (reason or None), plan_id, deliverable_id),
        )

    def get_deliverables(self, plan_id: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT deliverable_id, description, status, status_reason, updated_at
               FROM plan_deliverables WHERE plan_id=? ORDER BY deliverable_id ASC""",
            (plan_id,),
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

    def add_validation_run(
        self,
        task_id: int,
        kind: str,
        success: bool,
        command: str | None = None,
        output: str | None = None,
        *,
        status: str = "completed",
        return_code: int | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ):
        self._conn.execute(
            """INSERT INTO task_validation_runs
               (task_id, kind, command, status, return_code, success, output, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                kind,
                command,
                status,
                return_code,
                int(success),
                output,
                started_at,
                finished_at,
            ),
        )

    def get_validation_runs(self, task_id: int) -> list[dict]:
        rows = self._conn.execute(
            """SELECT kind, command, status, return_code, success, output, started_at, finished_at, created_at
               FROM task_validation_runs WHERE task_id=? ORDER BY id ASC""",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

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
            phase_name=row["phase_name"],
            deliverable_ids=json.loads(row["deliverable_ids"] or "[]"),
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
            priority=row["priority"],
        )

    def close(self):
        self._conn.close()
