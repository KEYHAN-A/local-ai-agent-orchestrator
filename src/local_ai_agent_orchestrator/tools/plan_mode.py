# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan-mode tools (``enter_plan_mode`` / ``exit_plan_mode``).

While plan mode is active, mutating tools (file_write, file_patch, destructive
shell_exec) are denied at the permission layer. The agent must propose a plan
and then call ``exit_plan_mode`` with an approval token before resuming writes.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Optional

from local_ai_agent_orchestrator.tools.base import (
    Tool,
    param,
    parameters_schema,
    register,
)
from local_ai_agent_orchestrator.tools.meta import (
    is_plan_mode,
    reset_plan_mode,
    set_plan_mode,
)

log = logging.getLogger(__name__)


_PLAN_TOKEN: ContextVar[Optional[object]] = ContextVar("lao_plan_token", default=None)
_LAST_PROPOSAL: ContextVar[Optional[str]] = ContextVar("lao_plan_proposal", default=None)


def enter_plan_mode(rationale: str = "") -> str:
    if is_plan_mode():
        return "OK: Already in plan mode."
    token = set_plan_mode(True)
    _PLAN_TOKEN.set(token)
    log.info(f"[PlanMode] entered ({rationale or 'no rationale'})")
    return (
        "OK: Plan mode active. Mutating tools are blocked. "
        "Propose the plan to the user, then call exit_plan_mode(approved_plan=..., approved=true) "
        "after the user approves."
    )


def exit_plan_mode(approved_plan: str = "", approved: bool = False) -> str:
    if not is_plan_mode():
        return "OK: Not in plan mode."
    if not approved:
        return (
            "ERROR: exit_plan_mode requires approved=true. "
            "Show the plan to the user and wait for explicit approval before exiting."
        )
    if not approved_plan.strip():
        return "ERROR: approved_plan must not be empty."
    _LAST_PROPOSAL.set(approved_plan.strip())
    token = _PLAN_TOKEN.get()
    if token is not None:
        try:
            reset_plan_mode(token)
        except Exception:
            set_plan_mode(False)
        _PLAN_TOKEN.set(None)
    else:
        set_plan_mode(False)
    log.info("[PlanMode] exited (approved)")
    return "OK: Plan mode exited. Mutating tools re-enabled."


def get_last_proposal() -> Optional[str]:
    return _LAST_PROPOSAL.get()


ENTER_PLAN_MODE = register(
    Tool(
        name="enter_plan_mode",
        description=(
            "Switch the agent into plan mode (no file_write, file_patch, or "
            "destructive shell). Use for ambiguous or design-heavy requests."
        ),
        parameters=parameters_schema(
            {"rationale": param("string", "Why plan mode is appropriate.")},
        ),
        call=enter_plan_mode,
        is_read_only=False,
        is_concurrency_safe=False,
        plan_mode_safe=True,
        prompt_contribution=(
            "enter_plan_mode(rationale) prevents mutating tools until the user "
            "approves the proposed plan via exit_plan_mode."
        ),
    )
)

EXIT_PLAN_MODE = register(
    Tool(
        name="exit_plan_mode",
        description=(
            "Leave plan mode after the user has explicitly approved the proposal."
        ),
        parameters=parameters_schema(
            {
                "approved_plan": param("string", "The plan text the user approved."),
                "approved": param(
                    "boolean",
                    "Must be true; set only after explicit user approval.",
                    default=False,
                ),
            },
            required=["approved_plan", "approved"],
        ),
        call=exit_plan_mode,
        is_read_only=False,
        is_concurrency_safe=False,
        plan_mode_safe=True,
        prompt_contribution=(
            "exit_plan_mode(approved_plan, approved=true) re-enables mutating tools. "
            "Never set approved=true without explicit user confirmation."
        ),
    )
)
