# SPDX-License-Identifier: GPL-3.0-or-later
"""Spec Doctor phase.

Consumes a locked ``IDEATION.md`` and produces a ``SPEC.md`` that:

- enumerates concrete, machine-verifiable Acceptance Criteria (``AC-N``),
- lists open BLOCKING questions that should bounce back to the user,
- captures non-goals, glossary entries, and a small risk register.

The architect downstream is expected to reference these AC IDs when assigning
``acceptance.acceptance_ids`` to its micro-tasks; the Contract Author then
turns each AC ID into an executable test.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.ideation import locked_path
from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.phases import _get_client, _llm_call, _strip_thinking_blocks
from local_ai_agent_orchestrator.prompts import build_spec_doctor_messages
from local_ai_agent_orchestrator.settings import get_settings

log = logging.getLogger(__name__)


_AC_PATTERN = re.compile(r"^\s*-?\s*\*{0,2}AC-(\d+)\*{0,2}\b", re.MULTILINE)
_BLOCKING_PATTERN = re.compile(r"BLOCKING", re.IGNORECASE)


def acceptance_ids_in(spec_md: str) -> list[str]:
    """Return ``["AC-1", "AC-2", ...]`` discovered in *spec_md*."""
    seen: dict[str, None] = {}
    for m in _AC_PATTERN.finditer(spec_md or ""):
        seen[f"AC-{int(m.group(1))}"] = None
    return list(seen.keys())


def blocking_questions_in(spec_md: str) -> list[str]:
    """Return BLOCKING-tagged lines from *spec_md*."""
    out: list[str] = []
    for line in (spec_md or "").splitlines():
        if _BLOCKING_PATTERN.search(line):
            stripped = line.strip()
            if stripped:
                out.append(stripped)
    return out


def _strip_outer_fence(text: str) -> str:
    """Remove an outer ```markdown ... ``` wrapper if the model added one."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```\w*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    return cleaned


def spec_doctor_phase(
    mm: ModelManager,
    workspace: Path,
    *,
    project_hint: Optional[str] = None,
    spec_filename: str = "SPEC.md",
) -> dict:
    """Run the Spec Doctor for the workspace.

    Reads ``.lao/ideation/locked.md`` (raising ``FileNotFoundError`` if it does
    not exist), invokes the analyst-class model, and writes the resulting
    SPEC.md alongside the workspace root. Returns a small report::

        {
          "spec_path": str,
          "acceptance_ids": list[str],
          "blocking_questions": list[str],
          "tokens": {"prompt": int, "completion": int},
        }

    The architect should be re-run after this phase so its tasks can reference
    the new acceptance IDs.
    """
    s = get_settings()
    if not getattr(s, "spec_doctor_enabled", True):
        log.info("[SpecDoctor] disabled in settings; skipping")
        return {"spec_path": "", "acceptance_ids": [], "blocking_questions": [], "tokens": {}}

    locked = locked_path(workspace)
    if not locked.exists():
        raise FileNotFoundError(
            f"Spec Doctor needs a locked IDEATION.md at {locked} — run /ideate then /lock first."
        )
    ideation_md = locked.read_text(encoding="utf-8")

    cfg = s.models.get("analyst") or s.models["planner"]
    role = "analyst" if "analyst" in s.models else "planner"
    model_key = mm.ensure_loaded(role)
    client = _get_client()
    messages = build_spec_doctor_messages(ideation_md, project_hint=project_hint)

    started = time.time()
    response = _llm_call(
        client,
        model_key,
        messages,
        max_tokens=min(getattr(cfg, "max_completion", 4096) or 4096, 4096),
        temperature=0.2,
        role=role,
    )
    duration = time.time() - started
    raw = response.choices[0].message.content or ""
    spec_md = _strip_outer_fence(_strip_thinking_blocks(raw))
    if not spec_md.strip():
        raise ValueError("Spec Doctor returned an empty document")

    spec_path = workspace / spec_filename
    spec_path.write_text(spec_md, encoding="utf-8")

    usage = getattr(response, "usage", None)
    ac_ids = acceptance_ids_in(spec_md)
    blocking = blocking_questions_in(spec_md)
    log.info(
        "[SpecDoctor] Wrote %s (%d AC, %d BLOCKING) in %.1fs",
        spec_path,
        len(ac_ids),
        len(blocking),
        duration,
    )
    return {
        "spec_path": str(spec_path),
        "acceptance_ids": ac_ids,
        "blocking_questions": blocking,
        "tokens": {
            "prompt": getattr(usage, "prompt_tokens", 0) if usage else 0,
            "completion": getattr(usage, "completion_tokens", 0) if usage else 0,
        },
        "duration_s": round(duration, 3),
    }
