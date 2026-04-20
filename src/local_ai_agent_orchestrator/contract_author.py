# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract Author phase.

Turns each task's declared acceptance criteria into EXECUTABLE acceptance
tests *before* the coder writes any production code. Output:

- Test files written into the workspace (e.g. ``tests/acceptance/...``).
- An ``acceptance.commands`` block persisted on the task so the acceptance
  runner and DONE gate can re-execute it deterministically.

Backed by the smaller analyst-class model. When the analyst phase has been
run, its inferred build/test hints feed directly into the Contract Author so
the tests speak the project's native runner.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.phases import _get_client, _llm_call, _strip_thinking_blocks
from local_ai_agent_orchestrator.prompts import build_contract_author_messages
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import MicroTask, TaskQueue

log = logging.getLogger(__name__)

_DEFAULT_TEST_DIR = "tests/acceptance"


def _infer_default_command(test_path: str, build_hint: Optional[str]) -> str:
    """Pick a runner command for a single test file based on extension + hint."""
    p = test_path.strip()
    lower = p.lower()
    hint = (build_hint or "").lower()
    if lower.endswith(".py"):
        return f"pytest -q {p}"
    if lower.endswith((".js", ".ts", ".tsx", ".jsx")):
        return f"npm test -- {p}"
    if lower.endswith(".rs"):
        return "cargo test"
    if "pytest" in hint:
        return f"pytest -q {p}"
    if "cargo" in hint:
        return "cargo test"
    if "npm" in hint or "yarn" in hint:
        return f"npm test -- {p}"
    return f"pytest -q {p}"


def _load_build_hint(workspace: Path) -> Optional[str]:
    """Best-effort runner hint derived from analyst_report.json (if present)."""
    report_path = workspace / "analyst_report.json"
    if not report_path.exists():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(report, dict):
        return None
    bs = report.get("build_system") or {}
    parts = []
    if bs.get("detected"):
        parts.append(f"Detected build system: {bs['detected']}")
    if bs.get("inferred_build_cmd"):
        parts.append(f"Build cmd: {bs['inferred_build_cmd']}")
    if bs.get("inferred_lint_cmd"):
        parts.append(f"Lint cmd: {bs['inferred_lint_cmd']}")
    test_layout = report.get("test_layout") or {}
    if test_layout.get("test_dirs"):
        parts.append(
            "Existing test directories: " + ", ".join(test_layout["test_dirs"])
        )
    return "\n".join(parts) if parts else None


def _extract_first_json_object(text: str) -> Optional[str]:
    """Find the first balanced JSON object in *text*; quote-aware."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return None


def _parse_contract_payload(content: str) -> dict:
    cleaned = _strip_thinking_blocks(content or "")
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```\w*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    blob = _extract_first_json_object(cleaned) or cleaned
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError as e:
        raise ValueError(f"Contract Author returned invalid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("Contract Author payload must be a JSON object")
    return payload


def _safe_relative(workspace: Path, candidate: str) -> Optional[Path]:
    """Resolve *candidate* under *workspace*; reject path escapes."""
    rel = (candidate or "").strip().lstrip("/").replace("\\", "/")
    if not rel:
        return None
    target = (workspace / rel).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        return None
    return target


def _materialise_tests(
    workspace: Path,
    tests_payload: object,
) -> tuple[list[str], list[str]]:
    """Write each test file. Returns (written_paths, skipped_paths)."""
    written: list[str] = []
    skipped: list[str] = []
    if not isinstance(tests_payload, list):
        return written, skipped
    for entry in tests_payload:
        if not isinstance(entry, dict):
            continue
        path_raw = str(entry.get("path") or "").strip()
        body = entry.get("content")
        if not path_raw or not isinstance(body, str):
            skipped.append(path_raw or "<missing path>")
            continue
        target = _safe_relative(workspace, path_raw)
        if target is None:
            skipped.append(path_raw)
            log.warning("[ContractAuthor] Refused to write outside workspace: %s", path_raw)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        written.append(str(target.relative_to(workspace.resolve())))
    return written, skipped


def _normalise_commands(
    payload_commands: object,
    test_paths: list[str],
    build_hint: Optional[str],
) -> list[str]:
    cmds: list[str] = []
    if isinstance(payload_commands, list):
        for c in payload_commands:
            s = str(c).strip()
            if s:
                cmds.append(s)
    elif isinstance(payload_commands, str) and payload_commands.strip():
        cmds.append(payload_commands.strip())
    if cmds:
        return cmds
    return [_infer_default_command(p, build_hint) for p in test_paths]


def contract_author_phase(
    mm: ModelManager,
    queue: TaskQueue,
    task: MicroTask,
    workspace: Path,
    *,
    spec_excerpt: Optional[str] = None,
) -> Optional[dict]:
    """Run the Contract Author for one task.

    Returns the persisted acceptance dict on success, or None when:
      * the task already has executable commands (idempotent), or
      * the task declares no acceptance_ids and no spec excerpt is provided.
    """
    s = get_settings()
    existing = task.acceptance if isinstance(task.acceptance, dict) else {}
    if existing.get("commands"):
        log.info("[ContractAuthor] Task #%s already has commands; skipping", task.id)
        return existing

    declared_ids = list(existing.get("acceptance_ids") or [])
    if not declared_ids and not spec_excerpt:
        log.info(
            "[ContractAuthor] Task #%s has no acceptance_ids and no SPEC excerpt; skipping",
            task.id,
        )
        return None

    cfg = s.models.get("analyst") or s.models["planner"]
    role = "analyst" if "analyst" in s.models else "planner"
    model_key = mm.ensure_loaded(role)
    client = _get_client()
    build_hint = _load_build_hint(workspace)

    messages = build_contract_author_messages(
        task, spec_excerpt=spec_excerpt, build_hint=build_hint
    )
    started = time.time()
    response = _llm_call(
        client,
        model_key,
        messages,
        max_tokens=min(cfg.max_completion, 2048),
        temperature=0.2,
        role=role,
    )
    duration = time.time() - started
    content = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)
    queue.log_run(
        task_id=task.id,
        phase="contract_author",
        model_key=model_key,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        duration_seconds=duration,
        success=True,
    )

    payload = _parse_contract_payload(content)
    written, skipped = _materialise_tests(workspace, payload.get("tests"))
    if skipped:
        log.warning(
            "[ContractAuthor] Task #%s skipped %d test entr(y|ies): %s",
            task.id,
            len(skipped),
            ", ".join(skipped[:5]),
        )

    test_paths = written or [str(t.get("path", "")).strip() for t in (payload.get("tests") or [])
                              if isinstance(t, dict) and t.get("path")]
    commands = _normalise_commands(payload.get("commands"), test_paths, build_hint)
    ids = [str(x).strip() for x in (payload.get("acceptance_ids") or declared_ids) if str(x).strip()]

    acceptance = {
        "acceptance_ids": ids,
        "tests": test_paths,
        "commands": commands,
        "allowed_major": int(existing.get("allowed_major", 0) or 0),
    }
    if existing.get("timeout_s"):
        acceptance["timeout_s"] = int(existing["timeout_s"])
    queue.set_task_acceptance(task.id, acceptance)
    log.info(
        "[ContractAuthor] Task #%s: %d test file(s), %d command(s), AC: %s",
        task.id,
        len(test_paths),
        len(commands),
        ", ".join(ids) or "—",
    )
    return acceptance


def author_contracts_for_plan(
    mm: ModelManager,
    queue: TaskQueue,
    plan_id: str,
    *,
    spec_excerpt: Optional[str] = None,
) -> int:
    """Run the Contract Author for every eligible task in *plan_id*.

    Returns the number of tasks that received fresh acceptance commands.
    """
    workspace = queue.workspace_for_plan(plan_id)
    written = 0
    for t in queue.get_plan_tasks(plan_id):
        try:
            result = contract_author_phase(mm, queue, t, workspace, spec_excerpt=spec_excerpt)
        except Exception as exc:  # keep the pipeline moving
            log.warning("[ContractAuthor] Task #%s failed: %s", t.id, exc)
            continue
        if result and result.get("commands"):
            written += 1
    return written
