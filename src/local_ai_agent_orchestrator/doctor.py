# SPDX-License-Identifier: GPL-3.0-or-later
"""
``lao doctor`` -- grouped, actionable diagnostics.

Mirrors the spirit of Claude Code's ``Doctor.tsx`` screen: each section
produces ``ok | warn | fail`` rows with terse remediation hints. Designed to
be safe to run in any environment (no LLM call required).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import TaskQueue


@dataclass
class Check:
    name: str
    status: str  # 'ok' | 'warn' | 'fail'
    detail: str = ""
    hint: str = ""


@dataclass
class Section:
    name: str
    checks: list[Check] = field(default_factory=list)


def _check_lm_studio() -> Check:
    try:
        import requests

        s = get_settings()
        r = requests.get(f"{s.lm_studio_base.rstrip('/')}/v1/models", timeout=3)
        if r.status_code == 200:
            return Check("LM Studio reachable", "ok", detail=s.lm_studio_base)
        return Check(
            "LM Studio reachable", "fail",
            detail=f"HTTP {r.status_code}",
            hint="Start LM Studio and enable the local server.",
        )
    except Exception as e:
        return Check(
            "LM Studio reachable", "fail",
            detail=str(e)[:120],
            hint="Start LM Studio and ensure the API endpoint is reachable.",
        )


def _check_models() -> list[Check]:
    out: list[Check] = []
    try:
        s = get_settings()
    except RuntimeError:
        return [Check("Settings initialised", "fail", hint="Run `lao init`.")]
    try:
        import requests

        r = requests.get(f"{s.lm_studio_base.rstrip('/')}/v1/models", timeout=3)
        loaded = []
        if r.status_code == 200:
            loaded = [m.get("id", "") for m in r.json().get("data", [])]
    except Exception:
        loaded = []
    for role, cfg in s.models.items():
        if cfg.key in loaded:
            out.append(Check(f"model[{role}]", "ok", detail=cfg.key))
        else:
            out.append(
                Check(
                    f"model[{role}]", "warn",
                    detail=f"{cfg.key} not currently loaded",
                    hint="Will be loaded on demand by ModelManager.",
                )
            )
    return out


def _check_memory_budget() -> Check:
    try:
        s = get_settings()
    except RuntimeError:
        return Check("RAM budget", "warn", "settings not initialised")
    if s.total_ram_gb is None:
        return Check(
            "RAM budget", "warn",
            detail="total_ram_gb unset",
            hint="Set total_ram_gb in factory.yaml so ModelManager can plan swaps.",
        )
    largest = max((cfg.size_bytes for cfg in s.models.values()), default=0)
    largest_gb = largest / 1_073_741_824
    if largest_gb > s.total_ram_gb * 0.9:
        return Check(
            "RAM budget", "fail",
            detail=f"largest model {largest_gb:.1f} GB exceeds 90% of {s.total_ram_gb} GB",
            hint="Pick a smaller role model or raise total_ram_gb.",
        )
    return Check("RAM budget", "ok", detail=f"largest model {largest_gb:.1f} GB / {s.total_ram_gb} GB")


def _check_git() -> Check:
    if shutil.which("git") is None:
        return Check("git available", "warn", hint="Install git to enable per-plan snapshots.")
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=3)
        return Check("git available", "ok", detail=r.stdout.strip())
    except Exception as e:
        return Check("git available", "warn", detail=str(e)[:120])


def _check_embedder() -> Check:
    try:
        s = get_settings()
        cfg = s.models.get("embedder")
    except RuntimeError:
        return Check("embedder configured", "fail", hint="Run `lao init`.")
    if cfg is None:
        return Check("embedder configured", "fail", hint="Add an `embedder` role to factory.yaml.")
    return Check("embedder configured", "ok", detail=cfg.key)


def _check_validators() -> Check:
    try:
        s = get_settings()
    except RuntimeError:
        return Check("validators", "warn")
    cmds = []
    if s.validation_build_cmd:
        cmds.append(f"build={s.validation_build_cmd}")
    if s.validation_lint_cmd:
        cmds.append(f"lint={s.validation_lint_cmd}")
    return Check(
        "validators",
        "ok" if cmds else "warn",
        detail=", ".join(cmds) or "none configured",
        hint="" if cmds else "Configure validation_build_cmd / validation_lint_cmd.",
    )


def _check_disk(path: Path) -> Check:
    try:
        usage = shutil.disk_usage(str(path))
        free_gb = usage.free / 1_073_741_824
        status = "ok" if free_gb > 5 else "warn"
        return Check(
            f"disk free ({path})",
            status,
            detail=f"{free_gb:.1f} GB free",
            hint="" if status == "ok" else "Free up disk to keep SQLite WAL healthy.",
        )
    except Exception as e:
        return Check("disk usage", "warn", detail=str(e)[:120])


def _check_schema() -> Check:
    try:
        q = TaskQueue()
    except Exception as e:
        return Check("schema", "fail", detail=str(e)[:120])
    expected = {
        "plans", "micro_tasks", "run_log", "plan_chunks", "task_findings",
        "plan_phases", "plan_deliverables", "task_validation_runs",
        "pilot_conversations", "task_todos", "memory_facts", "tool_audit",
    }
    rows = q._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    present = {r["name"] for r in rows}
    missing = expected - present
    if missing:
        return Check(
            "schema", "warn",
            detail=f"missing tables: {', '.join(sorted(missing))}",
            hint="They will be created on next TaskQueue() in production code.",
        )
    return Check("schema", "ok", detail=f"{len(present)} tables present")


def collect_sections() -> list[Section]:
    try:
        s = get_settings()
        config_dir = s.config_dir
    except RuntimeError:
        config_dir = Path.cwd()
    return [
        Section("LM Studio", [_check_lm_studio()]),
        Section("Models", _check_models()),
        Section("Resources", [_check_memory_budget(), _check_git(), _check_embedder()]),
        Section("Validation", [_check_validators()]),
        Section("Storage", [_check_disk(config_dir), _check_schema()]),
    ]


def _color(status: str, text: str) -> str:
    code = {"ok": "32", "warn": "33", "fail": "31"}.get(status, "0")
    return f"\033[{code}m{text}\033[0m"


def run_doctor(printer: Callable[[str], None] = print) -> int:
    """Print all sections; return 0 on clean, 1 on warnings, 2 on failures."""
    worst = "ok"
    severity = {"ok": 0, "warn": 1, "fail": 2}
    for section in collect_sections():
        printer(f"\n=== {section.name} ===")
        for c in section.checks:
            symbol = {"ok": "✓", "warn": "!", "fail": "✗"}.get(c.status, "?")
            line = f"  {_color(c.status, symbol)} {c.name}"
            if c.detail:
                line += f"  -- {c.detail}"
            if c.hint:
                line += f"\n      hint: {c.hint}"
            printer(line)
            if severity[c.status] > severity[worst]:
                worst = c.status
    if worst == "fail":
        return 2
    if worst == "warn":
        return 1
    return 0
