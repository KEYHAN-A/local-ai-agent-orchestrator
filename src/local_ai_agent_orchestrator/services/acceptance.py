# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance command runner.

Executes the acceptance commands declared on a task (or a whole plan) as
shell processes inside the workspace, captures structured pass/fail, and
records each run via :meth:`TaskQueue.add_validation_run` (kind='acceptance').

This is intentionally separate from the regular ``shell_exec`` tool: the
acceptance runner is a deterministic verifier, not an LLM tool, so it bypasses
the permissions prompt and just runs the contract author's commands.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.state import MicroTask, TaskQueue

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 180
MAX_OUTPUT_TAIL_LINES = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tail(text: str, n: int = MAX_OUTPUT_TAIL_LINES) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "...(truncated)...\n" + "\n".join(lines[-n:])


def _run_command(command: str, cwd: Path, timeout_s: int) -> dict:
    started = _now_iso()
    t0 = time.time()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        rc = int(proc.returncode)
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    except subprocess.TimeoutExpired as e:
        rc = 124
        out = f"TIMEOUT after {timeout_s}s\n{(e.stdout or '')}\n{(e.stderr or '')}"
    except FileNotFoundError as e:
        rc = 127
        out = f"COMMAND NOT FOUND: {e}"
    except Exception as e:  # pragma: no cover - defensive
        rc = 1
        out = f"ERROR: {e}"
    return {
        "command": command,
        "return_code": rc,
        "passed": rc == 0,
        "output": _tail(out),
        "started_at": started,
        "finished_at": _now_iso(),
        "duration_s": round(time.time() - t0, 3),
    }


def _normalise_acceptance(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    commands = payload.get("commands") or []
    if isinstance(commands, str):
        commands = [commands]
    commands = [str(c).strip() for c in commands if str(c).strip()]
    tests = payload.get("tests") or []
    if isinstance(tests, str):
        tests = [tests]
    tests = [str(t).strip() for t in tests if str(t).strip()]
    ac_ids = payload.get("acceptance_ids") or payload.get("ids") or []
    if isinstance(ac_ids, str):
        ac_ids = [ac_ids]
    ac_ids = [str(i).strip() for i in ac_ids if str(i).strip()]
    timeout = payload.get("timeout_s") or DEFAULT_TIMEOUT_S
    try:
        timeout = max(1, int(timeout))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    return {
        "commands": commands,
        "tests": tests,
        "acceptance_ids": ac_ids,
        "timeout_s": timeout,
        "allowed_major": int(payload.get("allowed_major", 0) or 0),
    }


def run_task_acceptance(
    queue: TaskQueue,
    task: MicroTask,
    workspace: Path,
    *,
    record: bool = True,
) -> dict:
    """Execute the acceptance commands for a single task.

    Returns a dict::

        {
          "task_id": int,
          "skipped": bool,            # True when no acceptance defined
          "passed": bool,             # all commands exit 0
          "runs": [<per-command result>],
          "tests": [...],             # echoed from the contract
          "acceptance_ids": [...],
        }
    """
    contract = _normalise_acceptance(task.acceptance or queue.get_task_acceptance(task.id))
    if not contract.get("commands"):
        return {
            "task_id": task.id,
            "skipped": True,
            "passed": True,
            "runs": [],
            "tests": contract.get("tests", []),
            "acceptance_ids": contract.get("acceptance_ids", []),
        }

    runs: list[dict] = []
    overall_pass = True
    timeout_s = int(contract.get("timeout_s") or DEFAULT_TIMEOUT_S)
    for cmd in contract["commands"]:
        result = _run_command(cmd, workspace, timeout_s)
        runs.append(result)
        if not result["passed"]:
            overall_pass = False
        if record:
            try:
                queue.add_validation_run(
                    task.id,
                    kind="acceptance",
                    success=result["passed"],
                    command=result["command"],
                    output=result["output"],
                    status="completed",
                    return_code=result["return_code"],
                    started_at=result["started_at"],
                    finished_at=result["finished_at"],
                )
            except Exception as exc:  # pragma: no cover - logging only
                log.debug("[Acceptance] add_validation_run failed: %s", exc)

    return {
        "task_id": task.id,
        "skipped": False,
        "passed": overall_pass,
        "runs": runs,
        "tests": contract.get("tests", []),
        "acceptance_ids": contract.get("acceptance_ids", []),
    }


def run_plan_acceptance(
    queue: TaskQueue,
    plan_id: str,
    workspace: Path,
    *,
    only_completed: bool = True,
    record: bool = True,
) -> dict:
    """Re-run every task's acceptance commands for an entire plan.

    Used by the Plan Integrator to detect regressions caused by later tasks.
    """
    results: list[dict] = []
    overall = True
    skipped_count = 0
    tasks = queue.get_plan_tasks(plan_id)
    for t in tasks:
        if only_completed and t.status != "completed":
            continue
        res = run_task_acceptance(queue, t, workspace, record=record)
        results.append(res)
        if res.get("skipped"):
            skipped_count += 1
            continue
        if not res.get("passed"):
            overall = False
    return {
        "plan_id": plan_id,
        "passed": overall,
        "task_results": results,
        "skipped_count": skipped_count,
        "evaluated_count": sum(1 for r in results if not r.get("skipped")),
    }
