# SPDX-License-Identifier: GPL-3.0-or-later
"""Workspace tools package.

This package replaces the old flat ``tools.py`` module. Public names are
re-exported here so existing imports (``from local_ai_agent_orchestrator.tools
import file_read, TOOL_SCHEMAS``) continue to work unchanged.

Each tool is a registered :class:`Tool` (see :mod:`tools.base`) so the OpenAI
function-calling schema and the dispatch map are derived deterministically
from a single source of truth.
"""

from __future__ import annotations

from local_ai_agent_orchestrator.tools import fs as _fs
from local_ai_agent_orchestrator.tools import memory_tools as _memory_tools
from local_ai_agent_orchestrator.tools import plan_mode as _plan_mode
from local_ai_agent_orchestrator.tools import search as _search
from local_ai_agent_orchestrator.tools import shell as _shell
from local_ai_agent_orchestrator.tools import skills_tools as _skills_tools
from local_ai_agent_orchestrator.tools import subagent as _subagent
from local_ai_agent_orchestrator.tools import todos as _todos
from local_ai_agent_orchestrator.tools.base import (
    PermissionDecision,
    Tool,
    all_tools,
    build_dispatch,
    build_openai_schemas,
    get,
    register,
    reset_registry,
    safe_json_dumps,
)
from local_ai_agent_orchestrator.tools.fs import (
    file_patch,
    file_read,
    file_write,
    list_dir,
)
from local_ai_agent_orchestrator.tools.meta import (
    allow_project_access,
    is_plan_mode,
    pick_pilot_tools_workspace,
    plan_mode,
    push_active_workspace,
    reset_active_workspace,
    reset_plan_mode,
    resolve_path,
    set_plan_mode,
    tools_workspace_root,
    tools_workspace_root as _workspace_root,  # backward-compat alias
    use_plan_workspace,
)
from local_ai_agent_orchestrator.tools.plan_mode import (
    enter_plan_mode,
    exit_plan_mode,
    get_last_proposal,
)
from local_ai_agent_orchestrator.tools.search import find_relevant_files
from local_ai_agent_orchestrator.tools.shell import shell_exec
from local_ai_agent_orchestrator.tools.memory_tools import (
    memory_append,
    memory_forget,
    memory_read,
)
from local_ai_agent_orchestrator.tools.skills_tools import (
    skill_clear,
    skill_list,
    skill_run,
)
from local_ai_agent_orchestrator.tools.subagent import agent_run
from local_ai_agent_orchestrator.tools.todos import (
    bind_queue as _bind_todo_queue,
    get_active_task,
    push_active_task,
    reset_active_task,
    task_todo_get,
    task_todo_set,
)


# ── Compatibility surface used by phases / pilot ──────────────────────


def _coder_tool_names() -> list[str]:
    """Tools exposed to the coder phase. TODO ledger + plan-mode toggles
    are included so the coder can self-organize."""
    return [
        "file_read",
        "file_write",
        "file_patch",
        "list_dir",
        "shell_exec",
        "task_todo_set",
        "task_todo_get",
        "enter_plan_mode",
        "exit_plan_mode",
        "memory_read",
        "memory_append",
        "skill_run",
        "skill_list",
    ]


# Backward-compatible globals used throughout the existing codebase.
TOOL_SCHEMAS = build_openai_schemas(_coder_tool_names())
TOOL_DISPATCH = build_dispatch(_coder_tool_names())


def refresh_tool_globals() -> None:
    """Recompute TOOL_SCHEMAS / TOOL_DISPATCH after dynamic registration."""
    global TOOL_SCHEMAS, TOOL_DISPATCH
    TOOL_SCHEMAS = build_openai_schemas(_coder_tool_names())
    TOOL_DISPATCH = build_dispatch(_coder_tool_names())


__all__ = [
    # contract
    "Tool",
    "PermissionDecision",
    "register",
    "get",
    "all_tools",
    "build_openai_schemas",
    "build_dispatch",
    "reset_registry",
    "refresh_tool_globals",
    "safe_json_dumps",
    # fs
    "file_read",
    "file_write",
    "file_patch",
    "list_dir",
    # shell
    "shell_exec",
    # search
    "find_relevant_files",
    # workspace plumbing
    "use_plan_workspace",
    "push_active_workspace",
    "reset_active_workspace",
    "pick_pilot_tools_workspace",
    "tools_workspace_root",
    "allow_project_access",
    "resolve_path",
    "is_plan_mode",
    "plan_mode",
    "set_plan_mode",
    "reset_plan_mode",
    # plan-mode tools
    "enter_plan_mode",
    "exit_plan_mode",
    "get_last_proposal",
    # todos
    "task_todo_set",
    "task_todo_get",
    "push_active_task",
    "reset_active_task",
    "get_active_task",
    # memory
    "memory_read",
    "memory_append",
    "memory_forget",
    # skills
    "skill_list",
    "skill_run",
    "skill_clear",
    # sub-agent
    "agent_run",
    # convenience globals
    "TOOL_SCHEMAS",
    "TOOL_DISPATCH",
]
