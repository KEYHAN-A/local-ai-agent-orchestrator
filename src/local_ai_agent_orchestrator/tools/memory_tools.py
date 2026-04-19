# SPDX-License-Identifier: GPL-3.0-or-later
"""Memory tools: ``memory_read``, ``memory_append``, ``memory_forget``."""

from __future__ import annotations

from local_ai_agent_orchestrator.services import memory as _memory
from local_ai_agent_orchestrator.tools.base import (
    Tool,
    param,
    parameters_schema,
    register,
)


def memory_read() -> str:
    block = _memory.read_memory_block()
    return block or "(memory is empty)"


def memory_append(fact: str, scope: str = "project") -> str:
    if scope not in {"project", "user"}:
        return "ERROR: scope must be 'project' or 'user'."
    inserted = _memory.append_fact(fact, scope=scope, source="manual")
    return "OK: appended" if inserted else "OK: fact already present (skipped)"


def memory_forget(fact_substring: str, scope: str = "project") -> str:
    if scope not in {"project", "user"}:
        return "ERROR: scope must be 'project' or 'user'."
    removed = _memory.forget_fact(fact_substring, scope=scope)
    return f"OK: removed {removed} line(s)"


MEMORY_READ = register(
    Tool(
        name="memory_read",
        description="Return the project + user memory blocks (LAO_MEMORY.md, ~/.lao/MEMORY.md).",
        parameters=parameters_schema({}),
        call=memory_read,
        is_read_only=True,
        is_concurrency_safe=True,
        plan_mode_safe=True,
    )
)

MEMORY_APPEND = register(
    Tool(
        name="memory_append",
        description="Append a single fact to project or user memory (idempotent).",
        parameters=parameters_schema(
            {
                "fact": param("string", "Fact text (one line)."),
                "scope": param(
                    "string",
                    "'project' (default) or 'user'.",
                    enum=["project", "user"],
                    default="project",
                ),
            },
            required=["fact"],
        ),
        call=memory_append,
        is_read_only=False,
        is_concurrency_safe=False,
        plan_mode_safe=True,
    )
)

MEMORY_FORGET = register(
    Tool(
        name="memory_forget",
        description="Remove every memory line containing the given substring.",
        parameters=parameters_schema(
            {
                "fact_substring": param("string", "Case-insensitive substring to drop."),
                "scope": param(
                    "string",
                    "'project' (default) or 'user'.",
                    enum=["project", "user"],
                    default="project",
                ),
            },
            required=["fact_substring"],
        ),
        call=memory_forget,
        is_read_only=False,
        is_concurrency_safe=False,
        plan_mode_safe=True,
    )
)
