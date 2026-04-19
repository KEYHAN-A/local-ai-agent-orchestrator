# SPDX-License-Identifier: GPL-3.0-or-later
"""Shell-execution tool with hard-blocked dangerous patterns and plan-mode gating."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

from local_ai_agent_orchestrator.tools.base import (
    PermissionDecision,
    Tool,
    param,
    parameters_schema,
    register,
)
from local_ai_agent_orchestrator.tools.meta import (
    is_plan_mode,
    resolve_path,
    tools_workspace_root,
)

log = logging.getLogger(__name__)


_BLOCKED_PATTERNS = (
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",
)

# Read-only command prefixes that are safe in plan mode and earn the
# is_read_only marker for permission rules.
_READ_ONLY_PREFIXES = (
    "ls",
    "cat",
    "head",
    "tail",
    "pwd",
    "echo ",
    "git status",
    "git diff",
    "git log",
    "git branch",
    "rg ",
    "grep ",
    "find ",
    "wc ",
    "stat ",
    "file ",
    "tree",
    "which ",
    "type ",
)


def _is_read_only_command(command: str) -> bool:
    cmd = (command or "").strip().lstrip("(").lstrip()
    for prefix in _READ_ONLY_PREFIXES:
        if cmd == prefix.strip() or cmd.startswith(prefix):
            return True
    return False


def shell_exec(command: str, timeout: int = 60, cwd: Optional[str] = None) -> str:
    """Execute a shell command within the workspace.

    Returns combined stdout+stderr, capped at 4000 chars and prefixed with the
    final exit code.
    """
    wr = tools_workspace_root()
    work_dir = resolve_path(cwd) if cwd else wr
    if not work_dir or not work_dir.is_dir():
        work_dir = wr

    for pattern in _BLOCKED_PATTERNS:
        if pattern in command:
            return f"ERROR: Blocked dangerous command pattern: {pattern}"

    log.info(f"[Tools] Shell: {command} (cwd={work_dir})")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n--- stderr ---\n" + result.stderr) if output else result.stderr
        if not output:
            output = "(no output)"
        output = f"exit_code: {result.returncode}\n{output}"
        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"
        return output
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


def _shell_permission_check(args: dict) -> PermissionDecision | None:
    """Plan mode allows only read-only commands."""
    if is_plan_mode() and not _is_read_only_command(str(args.get("command", ""))):
        return PermissionDecision.deny(
            reason="plan_mode_active",
            prompt="Only read-only shell commands (ls, cat, git status, ...) are "
            "allowed while plan mode is active.",
        )
    return None


SHELL_EXEC = register(
    Tool(
        name="shell_exec",
        description="Run a shell command in the workspace",
        parameters=parameters_schema(
            {
                "command": param("string", "Shell command to execute"),
                "timeout": param("integer", "Timeout in seconds (default 60)"),
            },
            required=["command"],
        ),
        call=shell_exec,
        is_read_only=False,
        is_concurrency_safe=False,
        check_permissions=_shell_permission_check,
        prompt_contribution=(
            "shell_exec runs in the active workspace. Prefer non-destructive "
            "commands; output is capped at ~4000 chars."
        ),
    )
)
