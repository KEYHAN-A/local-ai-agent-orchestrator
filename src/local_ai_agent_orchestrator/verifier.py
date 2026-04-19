# SPDX-License-Identifier: GPL-3.0-or-later
"""
Mechanical verifier phase that runs between coder and reviewer.

Cheap, deterministic checks catch the common hallucination classes that the
reviewer LLM tends to miss or be soft on:

- Files claimed in the coder summary actually exist on disk.
- Targeted ``task.file_paths`` exist and parse for their language.
- TODO ledger has no leftover ``pending`` / ``in_progress`` items.

When a check fails the verifier returns a structured payload that the runner
turns into a forced coder retry (without consuming a reviewer attempt).
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.state import MicroTask, TaskQueue

log = logging.getLogger(__name__)


_FILE_LIST_PATTERNS = (
    re.compile(r"Files\s+written:\s*([^\n]+)", re.IGNORECASE),
    re.compile(r"^Wrote:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
)


@dataclass
class VerifierIssue:
    severity: str  # 'critical' | 'major' | 'minor'
    issue_class: str
    message: str
    file_path: Optional[str] = None
    fix_hint: Optional[str] = None


@dataclass
class VerifierReport:
    ok: bool
    issues: list[VerifierIssue] = field(default_factory=list)
    files_checked: list[str] = field(default_factory=list)

    def add(self, issue: VerifierIssue) -> None:
        self.issues.append(issue)
        if issue.severity in {"critical", "major"}:
            self.ok = False

    def to_repair_text(self) -> str:
        if self.ok:
            return ""
        lines = ["Verifier rejected the coder output. Fix the following before reviewer:"]
        for it in self.issues:
            line = f"- [{it.severity}] {it.issue_class}"
            if it.file_path:
                line += f" ({it.file_path})"
            line += f": {it.message}"
            if it.fix_hint:
                line += f"  -- hint: {it.fix_hint}"
            lines.append(line)
        return "\n".join(lines)


def _claimed_files_from_output(output: str) -> list[str]:
    if not output:
        return []
    paths: list[str] = []
    for pat in _FILE_LIST_PATTERNS:
        for m in pat.finditer(output):
            chunk = m.group(1).strip()
            for raw in re.split(r"[,\n]", chunk):
                p = raw.strip().strip(".").strip("`").strip()
                if p and p not in paths:
                    paths.append(p)
    return paths


def _parse_python(path: Path, report: VerifierReport, rel: str) -> None:
    try:
        ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError as se:
        report.add(
            VerifierIssue(
                severity="critical",
                issue_class="SyntaxError",
                message=f"{se.msg} at line {se.lineno}",
                file_path=rel,
                fix_hint="Re-read the file with file_read and fix the syntax error.",
            )
        )


def _parse_json(path: Path, report: VerifierReport, rel: str) -> None:
    try:
        json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as je:
        report.add(
            VerifierIssue(
                severity="critical",
                issue_class="JSONDecodeError",
                message=f"{je.msg} at line {je.lineno}",
                file_path=rel,
                fix_hint="Re-read the file with file_read and emit valid JSON.",
            )
        )


def _parse_yaml(path: Path, report: VerifierReport, rel: str) -> None:
    try:
        import yaml

        yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as ye:
        report.add(
            VerifierIssue(
                severity="major",
                issue_class="YAMLError",
                message=str(ye)[:200],
                file_path=rel,
                fix_hint="Validate the YAML structure (indentation, quoting).",
            )
        )


def _check_path(workspace: Path, rel: str, report: VerifierReport) -> None:
    full = (workspace / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    try:
        full.relative_to(workspace.resolve())
    except ValueError:
        report.add(
            VerifierIssue(
                severity="major",
                issue_class="PathEscape",
                message=f"Path '{rel}' resolves outside the workspace.",
                file_path=rel,
            )
        )
        return
    if not full.exists():
        report.add(
            VerifierIssue(
                severity="critical",
                issue_class="MissingFile",
                message=f"Claimed file '{rel}' does not exist.",
                file_path=rel,
                fix_hint="Call file_write with the full intended content.",
            )
        )
        return
    if not full.is_file():
        return

    report.files_checked.append(rel)
    suffix = full.suffix.lower()
    if suffix == ".py":
        _parse_python(full, report, rel)
    elif suffix == ".json":
        _parse_json(full, report, rel)
    elif suffix in {".yaml", ".yml"}:
        _parse_yaml(full, report, rel)


def verify_task(
    task: MicroTask,
    workspace: Path,
    coder_output: str,
    queue: Optional[TaskQueue] = None,
) -> VerifierReport:
    """Run the mechanical checks for *task* and return a report."""
    report = VerifierReport(ok=True)
    workspace = workspace.resolve()

    # 1. Files declared in task.file_paths must exist & parse.
    for rel in task.file_paths or []:
        _check_path(workspace, rel, report)

    # 2. Files claimed in the coder summary must also exist.
    for rel in _claimed_files_from_output(coder_output):
        if rel in (task.file_paths or []):
            continue
        _check_path(workspace, rel, report)

    # 3. TODO ledger should not have unfinished entries when the coder declares done.
    if queue is not None:
        try:
            todos = queue.get_task_todos(task.id)
        except Exception:
            todos = []
        unfinished = [t for t in todos if t["status"] in {"pending", "in_progress"}]
        if todos and unfinished:
            report.add(
                VerifierIssue(
                    severity="major",
                    issue_class="UnfinishedTodos",
                    message=(
                        f"{len(unfinished)} TODO item(s) still pending: "
                        + ", ".join(f"{t['id']}({t['status']})" for t in unfinished[:5])
                    ),
                    fix_hint=(
                        "Complete every TODO via task_todo_set or mark cancelled with "
                        "an explicit reason before declaring done."
                    ),
                )
            )

    return report


def verifier_phase(
    queue: TaskQueue,
    task: MicroTask,
    coder_output: str,
) -> VerifierReport:
    """Public entry point used by the runner."""
    workspace = queue.workspace_for_plan(task.plan_id)
    report = verify_task(task, workspace, coder_output, queue=queue)

    # Persist findings so the operator can inspect them via existing tooling.
    for it in report.issues:
        try:
            queue.add_finding(
                task.id,
                source="verifier",
                severity=it.severity,
                issue_class=it.issue_class,
                message=it.message,
                file_path=it.file_path,
                fix_hint=it.fix_hint,
                analyzer_id="lao.verifier",
                analyzer_kind="static",
                confidence=1.0,
            )
        except Exception as e:
            log.debug(f"[Verifier] could not persist finding: {e}")

    if not report.ok:
        log.warning(
            "[Verifier] task #%s failed (%d issue(s)); forcing coder retry",
            task.id,
            len(report.issues),
        )
    return report
