# SPDX-License-Identifier: GPL-3.0-or-later
"""
Main orchestration loop for Local AI Agent Orchestrator.

Requires init_settings() to have been called before importing/using this module
from the CLI (or call run_* after init).
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.phases import (
    architect_phase,
    coder_phase,
    reviewer_phase,
)
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import TaskQueue

log = logging.getLogger(__name__)

_shutdown = False


def _signal_handler(sig, frame):
    global _shutdown
    log.info("\nShutdown requested. Finishing current task...")
    _shutdown = True


def setup_signals():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)


def run_factory(mm: ModelManager, queue: TaskQueue, single_run: bool = False):
    s = get_settings()
    queue.recover_interrupted()

    while not _shutdown:
        new_plans = _scan_for_new_plans(queue)
        for plan_file, plan_text, plan_id in new_plans:
            if _shutdown:
                break
            log.info(f"{'='*60}")
            log.info(f"New plan: {plan_file.name}")
            log.info(f"{'='*60}")
            try:
                architect_phase(mm, queue, plan_id, plan_text)
            except Exception as e:
                log.error(f"Architect phase failed for {plan_file.name}: {e}")
                continue

        processed = _process_queue(mm, queue)

        if not processed and not new_plans:
            if single_run:
                break
            _print_idle_status(queue)
            time.sleep(s.plan_watch_interval_s)

    _print_final_status(queue)


def _process_queue(mm: ModelManager, queue: TaskQueue) -> int:
    processed = 0

    while not _shutdown:
        task = queue.next_pending()
        if task:
            log.info(f"{'─'*40}")
            log.info(
                f"Coding task #{task.id}: {task.title} "
                f"(attempt {task.attempt + 1}/{task.max_attempts})"
            )

            try:
                coder_phase(mm, queue, task)
                processed += 1
            except Exception as e:
                log.error(f"Coder failed on task #{task.id}: {e}")
                if task.attempt + 1 >= task.max_attempts:
                    queue.mark_failed(task.id, str(e))
                else:
                    queue.mark_rework(task.id, f"Coder error: {e}")
                continue

            task = queue.get_task(task.id)
            if task and task.status == "coded":
                try:
                    reviewer_phase(mm, queue, task)
                    processed += 1
                except Exception as e:
                    log.error(f"Reviewer failed on task #{task.id}: {e}")
                    if task.attempt + 1 >= task.max_attempts:
                        queue.mark_failed(task.id, f"Reviewer error: {e}")
                    else:
                        queue.mark_rework(task.id, f"Reviewer error: {e}")
            continue

        task = queue.next_coded()
        if task:
            try:
                reviewer_phase(mm, queue, task)
                processed += 1
            except Exception as e:
                log.error(f"Reviewer failed on task #{task.id}: {e}")
                if task.attempt + 1 >= task.max_attempts:
                    queue.mark_failed(task.id, f"Reviewer error: {e}")
                else:
                    queue.mark_rework(task.id, f"Reviewer error: {e}")
            continue

        break

    return processed


def _scan_for_new_plans(queue: TaskQueue) -> list[tuple[Path, str, str]]:
    s = get_settings()
    s.plans_dir.mkdir(parents=True, exist_ok=True)
    new_plans = []

    for plan_file in sorted(s.plans_dir.glob("*.md")):
        try:
            plan_text = plan_file.read_text(encoding="utf-8")
        except Exception as e:
            log.warning(f"Could not read {plan_file}: {e}")
            continue

        if queue.is_plan_registered(plan_text):
            continue

        plan_id = queue.register_plan(plan_file.name, plan_text)
        new_plans.append((plan_file, plan_text, plan_id))

    return new_plans


def load_specific_plan(path: str, queue: TaskQueue) -> tuple[Path, str, str]:
    s = get_settings()
    plan_file = Path(path)
    if not plan_file.exists():
        plan_file = s.plans_dir / path
    if not plan_file.exists():
        raise FileNotFoundError(f"Plan file not found: {path}")

    plan_text = plan_file.read_text(encoding="utf-8")
    plan_id = queue.register_plan(plan_file.name, plan_text)
    return plan_file, plan_text, plan_id


def _print_idle_status(queue: TaskQueue):
    s = get_settings()
    stats = queue.get_stats()
    if stats:
        parts = [f"{status}: {count}" for status, count in sorted(stats.items())]
        log.info(f"Queue: {', '.join(parts)} | Watching {s.plans_dir}/ for new plans...")
    else:
        log.info(f"No tasks. Drop a .md plan file into {s.plans_dir}/ to start.")


def _print_final_status(queue: TaskQueue):
    stats = queue.get_stats()
    tokens = queue.get_total_tokens()
    log.info(f"\n{'='*60}")
    log.info("Factory Status:")
    for status, count in sorted(stats.items()):
        log.info(f"  {status:12s}: {count}")
    log.info(f"  Total tokens: {tokens['prompt_tokens'] + tokens['completion_tokens']:,}")
    log.info(f"{'='*60}")


def print_status(queue: TaskQueue):
    s = get_settings()
    stats = queue.get_stats()
    tokens = queue.get_total_tokens()

    print(f"\n{'='*60}")
    print("  Local AI Agent Orchestrator -- Status")
    print(f"{'='*60}")

    if not stats:
        print("  No tasks in queue.")
    else:
        total = sum(stats.values())
        print(f"\n  Task Queue ({total} total):")
        for status, count in sorted(stats.items()):
            bar = "#" * count
            print(f"    {status:12s}: {count:3d}  {bar}")

    print(f"\n  Token Usage:")
    print(f"    Prompt:     {tokens['prompt_tokens']:>10,}")
    print(f"    Completion: {tokens['completion_tokens']:>10,}")
    print(f"    Total:      {tokens['prompt_tokens'] + tokens['completion_tokens']:>10,}")

    print(f"\n  Paths:")
    print(f"    Database:   {s.db_path}")
    print(f"    Workspace:  {s.workspace_root}")
    print(f"    Plans:      {s.plans_dir}")
    print(f"{'='*60}\n")


def health_check(mm: ModelManager) -> bool:
    s = get_settings()
    print(f"\n{'='*60}")
    print("  Health Check")
    print(f"{'='*60}")

    ok = mm.health_check()
    print(f"\n  LM Studio Server: {'OK' if ok else 'UNREACHABLE'}")
    if not ok:
        print("  Start LM Studio and enable the local server.")
        return False

    missing = mm.verify_models_exist()
    available = mm.get_available_models()

    print(f"\n  Available Models ({len(available)}):")
    for key in available:
        role = None
        for r, cfg in s.models.items():
            if cfg.key == key:
                role = r
                break
        tag = f" <- {role}" if role else ""
        print(f"    {key}{tag}")

    if missing:
        print(f"\n  MISSING Models:")
        for m in missing:
            print(f"    {m}")
        print("\n  Download missing models in LM Studio before running.")
        return False

    print(f"\n  All required models present.")

    if s.total_ram_gb:
        print(f"\n  Configured RAM: {s.total_ram_gb} GB (for your reference / future tuning)")

    guardrails_ok = mm.check_guardrails()
    print(
        f"\n  Guardrails: {'OK (disabled or permissive)' if guardrails_ok else 'WARNING -- may block large models'}"
    )
    if not guardrails_ok:
        print("  Fix: LM Studio > Developer > Server Settings > Model Loading Guardrails > Off")

    print(f"{'='*60}\n")
    return True


def run_entry(
    *,
    plan: str | None = None,
    single_run: bool = False,
):
    """Main entry after CLI parsed args and init_settings() ran."""
    setup_signals()
    s = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    s.workspace_root.mkdir(parents=True, exist_ok=True)
    s.plans_dir.mkdir(parents=True, exist_ok=True)

    queue = TaskQueue()
    mm = ModelManager()

    if not mm.health_check():
        log.error("LM Studio server is not reachable at the configured endpoint.")
        sys.exit(1)

    missing = mm.verify_models_exist()
    if missing:
        log.error("Missing required models:")
        for m in missing:
            log.error(f"  {m}")
        sys.exit(1)

    if not mm.check_guardrails():
        log.warning(
            "LM Studio resource guardrails may block large models. "
            "Developer tab > Server Settings > Model Loading Guardrails > Off."
        )

    if s.total_ram_gb:
        log.info(f"Configured total RAM: {s.total_ram_gb} GB")

    log.info(f"{'='*60}")
    log.info("  Local AI Agent Orchestrator")
    log.info(f"  Models: {len(s.models)} configured")
    log.info(f"  Workspace: {s.workspace_root}")
    log.info(f"  Plans: {s.plans_dir}")
    log.info(f"  Database: {s.db_path}")
    log.info(f"{'='*60}")

    if plan:
        plan_file, plan_text, plan_id = load_specific_plan(plan, queue)
        log.info(f"Loaded plan: {plan_file.name}")
        tasks = queue.get_plan_tasks(plan_id)
        if not tasks:
            architect_phase(mm, queue, plan_id, plan_text)

    run_factory(mm, queue, single_run=single_run or bool(plan))
