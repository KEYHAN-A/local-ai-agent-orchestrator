# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-task TODO ledger tool (``task_todo_set``).

The coder is required to publish a TODO list before its first ``file_write`` so
that the verifier phase can confirm every deliverable was addressed.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING

from local_ai_agent_orchestrator.tools.base import (
    Tool,
    param,
    parameters_schema,
    register,
)

if TYPE_CHECKING:
    from local_ai_agent_orchestrator.state import TaskQueue

log = logging.getLogger(__name__)


_ACTIVE_TASK_ID: ContextVar[Optional[int]] = ContextVar("lao_active_task_id", default=None)
_QUEUE_REF: Optional["TaskQueue"] = None


def bind_queue(queue: Optional["TaskQueue"]) -> None:
    global _QUEUE_REF
    _QUEUE_REF = queue


def push_active_task(task_id: int) -> object:
    return _ACTIVE_TASK_ID.set(int(task_id))


def reset_active_task(token: object) -> None:
    _ACTIVE_TASK_ID.reset(token)


def get_active_task() -> Optional[int]:
    return _ACTIVE_TASK_ID.get()


def task_todo_set(items: list[dict]) -> str:
    """Replace the TODO list for the active task.

    Each item is ``{id, content, status}`` where status is one of
    ``pending|in_progress|completed|cancelled``. ``id`` may be a ``deliverable_id``
    or a free-form slug.
    """
    if _QUEUE_REF is None:
        return "ERROR: TODO queue not bound (call bind_queue first)."
    task_id = _ACTIVE_TASK_ID.get()
    if task_id is None:
        return "ERROR: No active task; task_todo_set is only valid inside a coder loop."
    if not isinstance(items, list):
        return "ERROR: 'items' must be a JSON array of {id,content,status}."
    cleaned: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        slug = str(raw.get("id") or "").strip() or f"todo_{len(cleaned)+1}"
        content = str(raw.get("content") or "").strip()
        status = str(raw.get("status") or "pending").strip().lower()
        if status not in {"pending", "in_progress", "completed", "cancelled"}:
            status = "pending"
        if not content:
            continue
        cleaned.append({"id": slug, "content": content, "status": status})
    if not cleaned:
        return "ERROR: No valid TODO items provided."
    try:
        _QUEUE_REF.set_task_todos(task_id, cleaned)
    except Exception as e:
        return f"ERROR: {e}"
    log.info(f"[Todos] task #{task_id}: {len(cleaned)} item(s)")
    return (
        f"OK: TODO ledger set for task #{task_id} ({len(cleaned)} item(s)). "
        "Mark each as in_progress / completed as you work."
    )


def task_todo_get() -> str:
    if _QUEUE_REF is None:
        return "ERROR: TODO queue not bound."
    task_id = _ACTIVE_TASK_ID.get()
    if task_id is None:
        return "ERROR: No active task."
    items = _QUEUE_REF.get_task_todos(task_id)
    if not items:
        return "(no TODOs set)"
    lines = [f"TODOs for task #{task_id}:"]
    for it in items:
        lines.append(f"  [{it['status']}] {it['id']}: {it['content']}")
    return "\n".join(lines)


TASK_TODO_SET = register(
    Tool(
        name="task_todo_set",
        description=(
            "Publish or update the TODO ledger for the current task. Call this "
            "BEFORE any file_write so the verifier can audit deliverable coverage."
        ),
        parameters=parameters_schema(
            {
                "items": param(
                    "array",
                    "List of {id, content, status} entries.",
                    items={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "cancelled"],
                            },
                        },
                        "required": ["content"],
                    },
                ),
            },
            required=["items"],
        ),
        call=task_todo_set,
        is_read_only=False,
        is_concurrency_safe=False,
        plan_mode_safe=True,
        prompt_contribution=(
            "task_todo_set([{id,content,status}, ...]) maintains the per-task TODO "
            "ledger. Required before the first file_write."
        ),
    )
)

TASK_TODO_GET = register(
    Tool(
        name="task_todo_get",
        description="Read the current TODO ledger for the active task.",
        parameters=parameters_schema({}),
        call=task_todo_get,
        is_read_only=True,
        is_concurrency_safe=True,
        plan_mode_safe=True,
    )
)
