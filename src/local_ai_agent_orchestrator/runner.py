# SPDX-License-Identifier: GPL-3.0-or-later
"""
Main orchestration loop for Local AI Agent Orchestrator.

Requires init_settings() to have been called before importing/using this module
from the CLI (or call run_* after init).
"""

from __future__ import annotations

import logging
import signal
from pathlib import Path

from local_ai_agent_orchestrator import plan_git
from local_ai_agent_orchestrator.interrupts import (
    interruptible_sleep,
    register_interrupt,
    reset_interrupt_state,
    should_shutdown,
)
from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.phases import (
    architect_phase,
    coder_phase,
    preflight_plan_context,
    reviewer_phase,
)
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import ReservedPlanStemError, TaskQueue
from local_ai_agent_orchestrator.reporting import write_quality_report
from local_ai_agent_orchestrator.tools import use_plan_workspace

log = logging.getLogger(__name__)

from local_ai_agent_orchestrator.unified_ui import apply_runner_context


def _signal_handler(sig, frame):
    count = register_interrupt()
    if count <= 1:
        log.info("\nShutdown requested. Finishing current task...")
        return
    log.warning("\nSecond interrupt received. Aborting immediately.")
    raise KeyboardInterrupt


def setup_signals():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)


def run_factory(
    mm: ModelManager,
    queue: TaskQueue,
    single_run: bool = False,
    *,
    use_tui: bool = False,
    ui: object | None = None,
):
    s = get_settings()
    queue.recover_interrupted()

    while not should_shutdown():
        new_plans = _scan_for_new_plans(queue)
        for plan_file, plan_text, plan_id in new_plans:
            if should_shutdown():
                break
            log.info(f"{'='*60}")
            log.info(f"New plan: {plan_file.name}")
            log.info(f"{'='*60}")
            apply_runner_context(phase="Architect", plan=plan_file.name, task="Decomposing plan")
            try:
                ws = queue.workspace_for_plan(plan_id)
                _seed_plan_metadata(queue, plan_id, plan_text)
                plan_git.snapshot_and_commit_plan(
                    ws,
                    plan_file.stem,
                    plan_file.name,
                    plan_text,
                    plan_id,
                )
                architect_phase(mm, queue, plan_id, plan_text, plan_file.name)
            except Exception as e:
                log.error(f"Architect phase failed for {plan_file.name}: {e}")
                continue

        processed = _process_queue(mm, queue)
        _mark_terminal_plans_completed(queue)
        if processed:
            for p in queue.get_plans():
                try:
                    write_quality_report(queue, p["id"], mm.get_metrics())
                except Exception as e:
                    log.warning(f"Quality report generation failed for {p['filename']}: {e}")

        if not processed and not new_plans:
            if single_run:
                break
            if s.pilot_mode_enabled:
                result = _enter_pilot_mode(mm, queue, use_tui=use_tui, ui=ui)
                from local_ai_agent_orchestrator.pilot import PilotResult
                if result == PilotResult.EXIT:
                    break
                elif result == PilotResult.RESUME_PIPELINE:
                    reset_interrupt_state()
                    continue
            else:
                _print_idle_status(queue)
                interruptible_sleep(s.plan_watch_interval_s)

    _print_final_status(queue, mm)


def _process_queue(mm: ModelManager, queue: TaskQueue) -> int:
    s = get_settings()
    processed = 0
    phase_filter = (s.execution_phase or "").strip() or None
    coder_cap = max(1, int(s.retry_cap_coder))
    reviewer_cap = max(1, int(s.retry_cap_reviewer))

    while not should_shutdown():
        if not s.phase_gated:
            task = queue.next_pending(phase_name=phase_filter)
            if task:
                try:
                    with use_plan_workspace(queue, task.plan_id):
                        coder_phase(mm, queue, task)
                    processed += 1
                except Exception as e:
                    log.error(f"Coder failed on task #{task.id}: {e}")
                    if task.attempt + 1 >= min(task.max_attempts, coder_cap):
                        queue.mark_failed(task.id, str(e), escalation_reason="coder_exception")
                    else:
                        queue.mark_rework(task.id, f"Coder error: {e}")
                task = queue.next_coded(phase_name=phase_filter)
                if task:
                    try:
                        with use_plan_workspace(queue, task.plan_id):
                            reviewer_phase(mm, queue, task)
                        processed += 1
                    except Exception as e:
                        log.error(f"Reviewer failed on task #{task.id}: {e}")
                        if task.attempt + 1 >= min(task.max_attempts, reviewer_cap):
                            queue.mark_failed(
                                task.id, f"Reviewer error: {e}", escalation_reason="reviewer_exception"
                            )
                        else:
                            queue.mark_rework(task.id, f"Reviewer error: {e}")
                continue

        batch = queue.next_pending_batch(limit=s.coder_batch_size, phase_name=phase_filter)
        for task in batch:
            log.info(f"{'─'*40}")
            log.info(
                f"Coding task #{task.id}: {task.title} "
                f"(attempt {task.attempt + 1}/{task.max_attempts})"
            )
            apply_runner_context(
                phase="Coder",
                task=f"#{task.id} {task.title}",
                attempt=f"{task.attempt + 1}/{task.max_attempts}",
            )
            try:
                with use_plan_workspace(queue, task.plan_id):
                    coder_phase(mm, queue, task)
                processed += 1
            except Exception as e:
                log.error(f"Coder failed on task #{task.id}: {e}")
                if task.attempt + 1 >= min(task.max_attempts, coder_cap):
                    queue.mark_failed(task.id, str(e), escalation_reason="coder_exception")
                else:
                    queue.mark_rework(task.id, f"Coder error: {e}")

        task = queue.next_coded(phase_name=phase_filter)
        reviewed = 0
        while task and reviewed < s.reviewer_batch_size:
            apply_runner_context(
                phase="Reviewer",
                task=f"#{task.id} {task.title}",
                attempt=f"{task.attempt + 1}/{task.max_attempts}",
            )
            try:
                with use_plan_workspace(queue, task.plan_id):
                    reviewer_phase(mm, queue, task)
                processed += 1
            except Exception as e:
                log.error(f"Reviewer failed on task #{task.id}: {e}")
                if task.attempt + 1 >= min(task.max_attempts, reviewer_cap):
                    queue.mark_failed(
                        task.id, f"Reviewer error: {e}", escalation_reason="reviewer_exception"
                    )
                else:
                    queue.mark_rework(task.id, f"Reviewer error: {e}")
            reviewed += 1
            task = queue.next_coded(phase_name=phase_filter)
        if reviewed:
            continue

        break

    return processed


def _scan_for_new_plans(queue: TaskQueue) -> list[tuple[Path, str, str]]:
    s = get_settings()
    s.plans_dir.mkdir(parents=True, exist_ok=True)
    new_plans = []

    for plan_file in sorted(s.plans_dir.glob("*.md")):
        if plan_file.name.upper() == "README.MD":
            continue
        try:
            plan_text = plan_file.read_text(encoding="utf-8")
        except Exception as e:
            log.warning(f"Could not read {plan_file}: {e}")
            continue

        if queue.is_plan_registered(plan_text):
            continue

        try:
            plan_id = queue.register_plan(plan_file.name, plan_text)
        except ReservedPlanStemError as e:
            log.warning("%s", e)
            continue
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
    _seed_plan_metadata(queue, plan_id, plan_text)
    return plan_file, plan_text, plan_id


def _seed_plan_metadata(queue: TaskQueue, plan_id: str, plan_text: str):
    phase_rows = []
    for idx, line in enumerate(plan_text.splitlines()):
        m = line.strip()
        if m.startswith("#") and "phase" in m.lower():
            phase_rows.append((idx, m.lstrip("# ").strip()))
    phase_names = [name for _, name in sorted(phase_rows, key=lambda x: x[0])]
    if phase_names:
        queue.upsert_plan_phases(plan_id, phase_names)

    deliverables: list[dict] = []
    for line in plan_text.splitlines():
        m = line.strip()
        if not m:
            continue
        did = None
        mt = None
        import re
        mt = re.search(r"\b([A-Z]{2,}-\d+)\b", m)
        if mt:
            did = mt.group(1)
        if did:
            deliverables.append({"id": did, "description": m})
    if deliverables:
        queue.upsert_deliverables(plan_id, deliverables)


def preflight_plan(path: str) -> bool:
    s = get_settings()
    plan_file = Path(path)
    if not plan_file.exists():
        plan_file = s.plans_dir / path
    if not plan_file.exists():
        log.error("Plan file not found: %s", path)
        return False
    plan_text = plan_file.read_text(encoding="utf-8")
    planner = s.models["planner"]
    result = preflight_plan_context(plan_text, planner.context_length, planner.max_completion)
    log.info("Preflight for %s", plan_file.name)
    log.info(
        "fit=%s prompt_est=%s target_ctx=%s chunks=%s",
        result["fit"],
        result["estimated_prompt_tokens"],
        result["target_context_tokens"],
        result["chunk_count"],
    )
    if not result["fit"]:
        log.warning("Plan exceeds single-pass planner context and will be chunked/fallback summarized.")
    return True


def _enter_pilot_mode(
    mm: ModelManager,
    queue: TaskQueue,
    *,
    use_tui: bool = False,
    ui: object | None = None,
) -> "PilotResult":
    """Transition from autopilot to pilot mode. Returns PilotResult.

    When a UnifiedUI is active, the transition is seamless -- no screen clear,
    no logging reconfiguration.  The unified UI just switches the status bar and
    shows an inline pipeline summary before handing input to the pilot agent.
    """
    from local_ai_agent_orchestrator.pilot import PilotResult
    from local_ai_agent_orchestrator.pilot_ui import enter_pilot_mode

    if ui is not None:
        from local_ai_agent_orchestrator.unified_ui import UnifiedUI
        if isinstance(ui, UnifiedUI):
            ui.update_status(phase="Pilot", task="Interactive chat", idle_hint="")
            ui.show_transition("Pipeline", "Pilot")

            report_rows = ui.build_idle_report()
            if report_rows:
                ui.show_report("Pipeline summary", report_rows)
            ui.snapshot_stats()
            ui.bell()

            result = _run_pilot_with_unified_ui(mm, queue, ui)

            if result == PilotResult.RESUME_PIPELINE:
                ui.show_transition("Pilot", "Pipeline")
                resume_rows = ui.build_resume_report()
                if resume_rows:
                    ui.show_report("Resuming pipeline", resume_rows)
                ui.update_status(phase="Resuming", task="Scanning for work")

            return result

    apply_runner_context(phase="Pilot", task="Interactive chat", idle_hint="")

    try:
        result = enter_pilot_mode(mm, queue, use_tui=use_tui)
    except KeyboardInterrupt:
        result = PilotResult.EXIT

    return result


def _run_pilot_with_unified_ui(
    mm: ModelManager,
    queue: TaskQueue,
    ui: "UnifiedUI",
) -> "PilotResult":
    """Create and run a PilotAgent wired to the UnifiedUI callbacks."""
    from local_ai_agent_orchestrator.pilot import PilotAgent, PilotResult

    queue.start_new_pilot_session()

    def _on_user_input() -> str | None:
        text = ui.prompt_user()
        if text is None:
            return None
        stripped = text.strip()
        if stripped and not stripped.startswith("/"):
            ui.show_user_message(stripped)
        return text

    agent = PilotAgent(
        mm,
        queue,
        on_assistant_message=ui.show_assistant_message,
        on_tool_call=ui.show_tool_call,
        on_tool_result=ui.show_tool_result,
        on_llm_round_begin=lambda hint: ui.show_thinking(hint),
        on_llm_round_end=lambda: None,
        on_tool_round_begin=lambda name: None,
        on_usage=ui.show_usage,
    )

    try:
        return agent.run(get_input=_on_user_input)
    except KeyboardInterrupt:
        return PilotResult.EXIT


def _print_idle_status(queue: TaskQueue):
    s = get_settings()
    stats = queue.get_stats()
    if stats:
        parts = [f"{status}: {count}" for status, count in sorted(stats.items())]
        msg = f"Queue: {', '.join(parts)} | Watching {s.plans_dir}/ for new plans..."
        log.info(msg)
        apply_runner_context(phase="Watching", idle_hint=msg, task="—")
    else:
        msg = f"No tasks. Drop a .md plan file into {s.plans_dir}/ to start."
        log.info(msg)
        apply_runner_context(phase="Watching", idle_hint=msg, task="—")


def _print_final_status(queue: TaskQueue, mm: ModelManager | None = None):
    stats = queue.get_stats()
    tokens = queue.get_total_tokens()
    eff = queue.get_efficiency_metrics()
    log.info(f"\n{'='*60}")
    log.info("Factory Status:")
    for status, count in sorted(stats.items()):
        log.info(f"  {status:12s}: {count}")
    log.info(f"  Total tokens: {tokens['prompt_tokens'] + tokens['completion_tokens']:,}")
    log.info(
        f"  Run-log model_key changes: {eff.get('model_switches', 0)} "
        f"(successive run_log rows with different model_key)"
    )
    if mm is not None:
        m = mm.get_metrics()
        log.info(
            f"  LM Studio swap cycles: {m.get('swap_count', 0)} "
            f"(unload+load after another LLM was resident)"
        )
        log.info(
            f"  LM Studio loads / unloads: {m.get('load_count', 0)} / {m.get('unload_count', 0)}"
        )
    log.info(f"{'='*60}")
    if should_shutdown():
        log.info("Goodbye from LAO.")
        log.info("Continue this session later with: lao run")
        log.info("Need setup/model checks first? Run: lao")
        log.info("Website: https://lao.keyhan.info")


def _mark_terminal_plans_completed(queue: TaskQueue):
    s = get_settings()
    for p in queue.get_plans():
        if p.get("status") == "completed":
            continue
        if queue.is_plan_closure_satisfied(
            p["id"],
            strict_adherence=bool(getattr(s, "strict_adherence", False)),
            allowed_statuses=set(getattr(s, "strict_closure_allowed_statuses", ["validated"])),
        ):
            queue.mark_plan_completed(p["id"])


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
    print(f"    Config dir: {s.config_dir}")
    print(f"    Database:   {s.db_path}")
    print(f"    Project dirs: {s.config_dir}/<plan-stem>/ (per plan, same stem as plans/*.md)")
    print(f"    Fallback:   {s.workspace_root}")
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
    use_tui: bool = False,
) -> bool:
    """Main entry after CLI parsed args and init_settings() ran."""
    reset_interrupt_state()
    setup_signals()
    s = get_settings()

    ui = None
    if use_tui:
        from local_ai_agent_orchestrator.unified_ui import UnifiedUI

        history_path = s.config_dir / ".lao" / "chat_history"
        ui = UnifiedUI(history_path=history_path)
        ui.start()
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )

    queue: TaskQueue | None = None
    mm: ModelManager | None = None
    try:
        (s.config_dir / ".lao").mkdir(parents=True, exist_ok=True)
        s.workspace_root.mkdir(parents=True, exist_ok=True)
        s.plans_dir.mkdir(parents=True, exist_ok=True)

        queue = TaskQueue()
        if ui is not None:
            ui.set_queue_getter(lambda: queue)

        mm = ModelManager()

        if not mm.health_check():
            apply_runner_context(
                phase="Blocked",
                task="LM Studio unreachable",
                idle_hint="Start LM Studio server and run `lao` again.",
            )
            log.error("LM Studio server is not reachable at the configured endpoint.")
            log.error("Start LM Studio, enable local server, then retry.")
            return False

        missing = mm.verify_models_exist()
        if missing:
            apply_runner_context(
                phase="Blocked",
                task="Missing model mappings",
                idle_hint="Run `lao configure-models` to remap roles.",
            )
            log.error("Missing required models:")
            for m in missing:
                log.error(f"  {m}")
            log.error("Run `lao configure-models` to update model names for each role.")
            log.error("Tip: list available keys with `lms ls` or `lao health`.")
            return False

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
        log.info(f"  Per-plan project dirs: {s.config_dir}/<plan-stem>/")
        log.info(f"  Plans: {s.plans_dir}")
        log.info(f"  Database: {s.db_path}")
        log.info(f"{'='*60}")

        if plan:
            try:
                plan_file, plan_text, plan_id = load_specific_plan(plan, queue)
            except ReservedPlanStemError as e:
                log.error("%s", e)
                return False
            log.info(f"Loaded plan: {plan_file.name}")
            tasks = queue.get_plan_tasks(plan_id)
            if not tasks:
                apply_runner_context(
                    phase="Architect",
                    plan=plan_file.name,
                    task="Decomposing plan",
                )
                ws = queue.workspace_for_plan(plan_id)
                plan_git.snapshot_and_commit_plan(
                    ws,
                    plan_file.stem,
                    plan_file.name,
                    plan_text,
                    plan_id,
                )
                architect_phase(mm, queue, plan_id, plan_text, plan_file.name)

        if s.architect_only:
            log.info("Architect-only mode enabled; skipping coder/reviewer processing.")
            return True
        run_factory(
            mm, queue,
            single_run=single_run or bool(plan),
            use_tui=use_tui,
            ui=ui,
        )
        return True
    finally:
        if ui is not None and queue is not None:
            ui.print_run_summary(queue, model_metrics=mm.get_metrics() if mm else None)
        if ui is not None:
            ui.stop()
        if use_tui:
            root = logging.getLogger()
            root.handlers.clear()
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
