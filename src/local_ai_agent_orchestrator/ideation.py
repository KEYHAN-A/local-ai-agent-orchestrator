# SPDX-License-Identifier: GPL-3.0-or-later
"""Pilot-driven ideation flow.

The Pilot exposes three slash commands that wrap this module:

- ``/ideate <topic>``  → start a fresh ``IDEATION.md`` draft and return the
  Ideator's first set of clarifying questions.
- ``/ideate <reply>``  → continue refining the draft (each turn replaces the
  draft with the Ideator's latest version).
- ``/lock``            → mark the current ``IDEATION.md`` as locked so the Spec
  Doctor can consume it without further edits.

State is kept on disk inside ``<project>/.lao/ideation/`` so the workflow
survives session restarts:

- ``IDEATION.md``      — current draft (overwritten each turn)
- ``locked.md``        — copy made when ``/lock`` is invoked
- ``conversation.json``— rolling list of ``{"role", "content"}`` turns
- ``status.json``      — small bag of metadata (started_at, locked_at, topic)

This module is intentionally I/O-only; it does NOT call the LLM. The Pilot
is responsible for invoking :func:`ideation_dir` to read the current draft,
calling the model with :func:`build_ideation_messages`, then handing the
result back to :func:`apply_ideator_turn`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ideation_dir(workspace: Path) -> Path:
    """Return (and create) the per-project ideation directory."""
    target = workspace / ".lao" / "ideation"
    target.mkdir(parents=True, exist_ok=True)
    return target


def draft_path(workspace: Path) -> Path:
    return ideation_dir(workspace) / "IDEATION.md"


def locked_path(workspace: Path) -> Path:
    return ideation_dir(workspace) / "locked.md"


def status_path(workspace: Path) -> Path:
    return ideation_dir(workspace) / "status.json"


def conversation_path(workspace: Path) -> Path:
    return ideation_dir(workspace) / "conversation.json"


def read_status(workspace: Path) -> dict:
    p = status_path(workspace)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_status(workspace: Path, status: dict) -> None:
    status_path(workspace).write_text(
        json.dumps(status, indent=2, sort_keys=True), encoding="utf-8"
    )


def read_history(workspace: Path) -> list[dict]:
    p = conversation_path(workspace)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict) and "role" in t and "content" in t]
    except Exception:
        return []
    return []


def _write_history(workspace: Path, history: list[dict]) -> None:
    conversation_path(workspace).write_text(
        json.dumps(history, indent=2), encoding="utf-8"
    )


def read_draft(workspace: Path) -> Optional[str]:
    p = draft_path(workspace)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def is_locked(workspace: Path) -> bool:
    return bool(read_status(workspace).get("locked_at"))


def start_ideation(workspace: Path, topic: str) -> dict:
    """Reset the draft and seed status with the topic.

    The draft itself is not yet written — the Ideator will produce the first
    pass on the next turn.  ``conversation.json`` is reset.
    """
    topic = (topic or "").strip()
    _write_history(workspace, [])
    if draft_path(workspace).exists():
        draft_path(workspace).unlink()
    if locked_path(workspace).exists():
        locked_path(workspace).unlink()
    status = {
        "started_at": _now_iso(),
        "topic": topic,
        "locked_at": None,
        "turns": 0,
    }
    _write_status(workspace, status)
    return status


_DRAFT_FENCE = re.compile(
    r"```(?:markdown|md)?\s*\n(?P<body>.+?)\n```", re.DOTALL | re.IGNORECASE
)


def extract_draft(assistant_text: str) -> Optional[str]:
    """Pull the IDEATION.md body out of an Ideator response.

    The Ideator is instructed to wrap the full current draft in a fenced
    ```markdown code block. We pick the FIRST such block. If no fence is
    present we fall back to the entire response as the draft.
    """
    if not assistant_text:
        return None
    m = _DRAFT_FENCE.search(assistant_text)
    if m:
        body = m.group("body").strip()
        return body if body else None
    text = assistant_text.strip()
    return text or None


def apply_ideator_turn(
    workspace: Path,
    user_text: str,
    assistant_text: str,
) -> dict:
    """Persist a single ideation turn and refresh the draft.

    Returns the updated status dict.
    """
    status = read_status(workspace) or {"started_at": _now_iso(), "turns": 0}
    history = read_history(workspace)
    if user_text:
        history.append({"role": "user", "content": user_text})
    if assistant_text:
        history.append({"role": "assistant", "content": assistant_text})
    _write_history(workspace, history)

    draft = extract_draft(assistant_text)
    if draft:
        draft_path(workspace).write_text(draft, encoding="utf-8")

    status["turns"] = int(status.get("turns") or 0) + 1
    status["last_turn_at"] = _now_iso()
    _write_status(workspace, status)
    return status


def lock_ideation(workspace: Path) -> Path:
    """Freeze the current IDEATION.md by copying it to ``locked.md``.

    Raises ``FileNotFoundError`` if no draft exists yet.
    """
    src = draft_path(workspace)
    if not src.exists():
        raise FileNotFoundError(
            "No IDEATION.md draft to lock — start with /ideate first."
        )
    dest = locked_path(workspace)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    status = read_status(workspace) or {}
    status["locked_at"] = _now_iso()
    _write_status(workspace, status)
    return dest


def unlock_ideation(workspace: Path) -> None:
    """Reverse :func:`lock_ideation` (used by tests / explicit reset)."""
    if locked_path(workspace).exists():
        locked_path(workspace).unlink()
    status = read_status(workspace) or {}
    status["locked_at"] = None
    _write_status(workspace, status)


def blocking_questions(workspace: Path) -> list[str]:
    """Return any open BLOCKING questions found in the draft."""
    draft = read_draft(workspace) or ""
    out: list[str] = []
    for line in draft.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "BLOCKING" in stripped and ("?" in stripped or stripped.startswith(("-", "*"))):
            out.append(stripped)
    return out
