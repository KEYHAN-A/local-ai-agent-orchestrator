# SPDX-License-Identifier: GPL-3.0-or-later
"""``skill_run`` and ``skill_list`` tools (Tier 2 skills system)."""

from __future__ import annotations

from local_ai_agent_orchestrator import skills as _skills
from local_ai_agent_orchestrator.tools.base import (
    Tool,
    param,
    parameters_schema,
    register,
)


def skill_list() -> str:
    items = _skills.list_skills()
    if not items:
        return "(no skills loaded)"
    lines = ["Available skills:"]
    for s in items:
        lines.append(f"  - {s.name}: {s.description or '(no description)'}")
    return "\n".join(lines)


def skill_run(name: str) -> str:
    sk = _skills.activate(name)
    if sk is None:
        return f"ERROR: skill '{name}' not found. Use skill_list to see available skills."
    return (
        f"OK: skill '{sk.name}' activated. Its system-prompt addendum will be "
        "applied on the next phase build."
    )


def skill_clear() -> str:
    _skills.deactivate()
    return "OK: active skill cleared."


SKILL_LIST = register(
    Tool(
        name="skill_list",
        description="List loaded skills (bundled + user) and their descriptions.",
        parameters=parameters_schema({}),
        call=skill_list,
        is_read_only=True,
        is_concurrency_safe=True,
        plan_mode_safe=True,
    )
)

SKILL_RUN = register(
    Tool(
        name="skill_run",
        description=(
            "Activate a named skill so its addendum is applied to subsequent phases."
        ),
        parameters=parameters_schema(
            {"name": param("string", "Skill name (see skill_list).")},
            required=["name"],
        ),
        call=skill_run,
        is_read_only=False,
        is_concurrency_safe=False,
        plan_mode_safe=True,
    )
)

SKILL_CLEAR = register(
    Tool(
        name="skill_clear",
        description="Deactivate the currently-active skill.",
        parameters=parameters_schema({}),
        call=skill_clear,
        is_read_only=False,
        is_concurrency_safe=False,
        plan_mode_safe=True,
    )
)
