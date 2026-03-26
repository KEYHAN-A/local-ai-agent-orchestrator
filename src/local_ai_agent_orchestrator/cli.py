# SPDX-License-Identifier: GPL-3.0-or-later
"""
Command-line interface for Local AI Agent Orchestrator.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from local_ai_agent_orchestrator import interactive_ui as ui
from local_ai_agent_orchestrator.settings import init_settings
from local_ai_agent_orchestrator.state import TaskQueue


def _default_config_path(cwd: Path) -> Path | None:
    for name in ("factory.yaml", "factory.yml"):
        p = cwd / name
        if p.is_file():
            return p
    return None


def _write_example_config(dest: Path) -> None:
    example = {
        "lm_studio_base_url": "http://127.0.0.1:1234",
        "openai_api_key": "lm-studio",
        "total_ram_gb": 36,
        "paths": {
            "plans": "./plans",
            "database": "./.lao/state.db",
        },
        "memory_gate": {
            "release_fraction": 0.75,
            "swap_growth_limit_mb": 512,
            "settle_timeout_s": 60,
            "poll_interval_s": 2,
        },
        "orchestration": {
            "model_load_timeout_s": 180,
            "max_task_attempts": 3,
            "plan_watch_interval_s": 10,
            "llm_request_timeout_s": 300,
            "llm_retry_attempts": 3,
            "llm_retry_backoff_base_s": 5,
            "phase_gated": True,
            "coder_batch_size": 4,
            "reviewer_batch_size": 6,
            "max_context_utilization": 0.85,
            "quality_gate_mode": "standard",
            "validation_build_cmd": None,
            "validation_lint_cmd": None,
            "validation_profile": "default",
            "validation_profiles": {
                "default": {
                    "commands": [],
                    "block_on_severities": ["critical", "major"],
                    "block_min_confidence": 0.6,
                    "block_min_confidence_by_analyzer_kind": {},
                    "block_min_confidence_by_analyzer_id": {},
                }
            },
            "placeholder_max_markers_per_kloc": 3.0,
            "placeholder_max_ratio": 0.02,
            "preflight_reserved_tokens": 256,
            "strict_adherence": False,
            "strict_closure_allowed_statuses": ["validated"],
            "retry_cooldown_base_s": 30,
            "retry_cap_coder": 3,
            "retry_cap_reviewer": 3,
            "retry_cap_validation": 3,
            "no_progress_repeat_limit": 2,
            "benchmark_min_pass_rate": 0.85,
            "benchmark_fail_on_regression": True,
        },
        "git": {
            "enabled": True,
            "plan_file_name": "LAO_PLAN.md",
            "commit_trailers": False,
        },
        "models": {
            "planner": {
                "key": "qwen_qwen3.5-35b-a3b",
                "context_length": 32768,
                "max_completion": 16384,
                "supports_tools": True,
                "size_bytes": 21513639040,
                "description": "Architect / planner",
            },
            "coder": {
                "key": "qwen/qwen3-coder-30b",
                "context_length": 16384,
                "max_completion": 4096,
                "supports_tools": True,
                "size_bytes": 17190972664,
                "description": "Coder",
            },
            "reviewer": {
                "key": "deepseek-r1-distill-qwen-32b",
                "context_length": 8192,
                "max_completion": 2048,
                "supports_tools": False,
                "size_bytes": 18500000000,
                "description": "Reviewer (DeepSeek R1 Distill Qwen 32B)",
            },
            "embedder": {
                "key": "text-embedding-nomic-embed-text-v1.5",
                "context_length": 2048,
                "max_completion": 0,
                "supports_tools": False,
                "size_bytes": 84106624,
                "description": "Embeddings for semantic search",
            },
        },
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        yaml.dump(example, f, default_flow_style=False, sort_keys=False)
    print(f"Wrote example configuration: {dest}")


def _default_model_profiles() -> dict[str, dict[str, str]]:
    return {
        "small": {
            "planner": "qwen2.5-7b-instruct",
            "coder": "qwen2.5-coder-7b-instruct",
            "reviewer": "qwen2.5-7b-instruct",
            "embedder": "text-embedding-nomic-embed-text-v1.5",
        },
        "medium": {
            "planner": "qwen_qwen3.5-35b-a3b",
            "coder": "qwen/qwen3-coder-30b",
            "reviewer": "deepseek-r1-distill-qwen-32b",
            "embedder": "text-embedding-nomic-embed-text-v1.5",
        },
        "large": {
            "planner": "qwen_qwen3.5-35b-a3b",
            "coder": "qwen/qwen3-coder-30b",
            "reviewer": "deepseek/deepseek-r1",
            "embedder": "text-embedding-nomic-embed-text-v1.5",
        },
    }


def _build_config_from_inputs(
    lm_studio_base_url: str,
    total_ram_gb: float,
    model_keys: dict[str, str],
) -> dict[str, Any]:
    return {
        "lm_studio_base_url": lm_studio_base_url,
        "openai_api_key": "lm-studio",
        "total_ram_gb": total_ram_gb,
        "paths": {
            "plans": "./plans",
            "database": "./.lao/state.db",
        },
        "memory_gate": {
            "release_fraction": 0.75,
            "swap_growth_limit_mb": 512,
            "settle_timeout_s": 60,
            "poll_interval_s": 2,
        },
        "orchestration": {
            "model_load_timeout_s": 180,
            "max_task_attempts": 3,
            "plan_watch_interval_s": 10,
            "llm_request_timeout_s": 300,
            "llm_retry_attempts": 3,
            "llm_retry_backoff_base_s": 5,
            "phase_gated": True,
            "coder_batch_size": 4,
            "reviewer_batch_size": 6,
            "max_context_utilization": 0.85,
            "quality_gate_mode": "standard",
            "validation_build_cmd": None,
            "validation_lint_cmd": None,
            "validation_profile": "default",
            "validation_profiles": {
                "default": {
                    "commands": [],
                    "block_on_severities": ["critical", "major"],
                    "block_min_confidence": 0.6,
                    "block_min_confidence_by_analyzer_kind": {},
                    "block_min_confidence_by_analyzer_id": {},
                }
            },
            "placeholder_max_markers_per_kloc": 3.0,
            "placeholder_max_ratio": 0.02,
            "preflight_reserved_tokens": 256,
            "strict_adherence": False,
            "strict_closure_allowed_statuses": ["validated"],
            "retry_cooldown_base_s": 30,
            "retry_cap_coder": 3,
            "retry_cap_reviewer": 3,
            "retry_cap_validation": 3,
            "no_progress_repeat_limit": 2,
            "benchmark_min_pass_rate": 0.85,
            "benchmark_fail_on_regression": True,
        },
        "git": {
            "enabled": True,
            "plan_file_name": "LAO_PLAN.md",
            "commit_trailers": False,
        },
        "models": {
            "planner": {
                "key": model_keys["planner"],
                "context_length": 32768,
                "max_completion": 16384,
                "supports_tools": True,
                "size_bytes": 21513639040,
                "description": "Architect / planner",
            },
            "coder": {
                "key": model_keys["coder"],
                "context_length": 16384,
                "max_completion": 4096,
                "supports_tools": True,
                "size_bytes": 17190972664,
                "description": "Coder",
            },
            "reviewer": {
                "key": model_keys["reviewer"],
                "context_length": 8192,
                "max_completion": 2048,
                "supports_tools": False,
                "size_bytes": 18500000000,
                "description": "Reviewer",
            },
            "embedder": {
                "key": model_keys["embedder"],
                "context_length": 2048,
                "max_completion": 0,
                "supports_tools": False,
                "size_bytes": 84106624,
                "description": "Embeddings for semantic search",
            },
        },
    }


def _resolve_config_path(cwd: Path, cli_config: Path | None) -> Path | None:
    cfg_path = cli_config or _default_config_path(cwd)
    env_config = os.getenv("LAO_CONFIG") or os.getenv("FACTORY_CONFIG")
    if env_config and Path(env_config).is_file():
        cfg_path = Path(env_config)
    return cfg_path


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def _run_init(cwd: Path, *, skip_readme: bool, no_interactive: bool) -> None:
    from local_ai_agent_orchestrator.console_ui import write_workspace_readme

    interactive = ui.is_tty() and not no_interactive
    if interactive:
        ui.print_header(
            "Interactive Workspace Setup",
            "Create config, verify model strategy, and bootstrap your first plan.",
        )
        ui.print_info("This setup takes about 1-2 minutes.")

    _write_example_config(cwd / "factory.example.yaml")
    (cwd / ".lao").mkdir(parents=True, exist_ok=True)
    (cwd / "plans").mkdir(exist_ok=True)
    if not skip_readme and write_workspace_readme(cwd):
        ui.print_info("Created README.md (workspace guide).")

    if not interactive:
        print("Created .lao/, plans/, and factory.example.yaml.")
        print("Copy factory.example.yaml to factory.yaml and edit model keys (see `lms ls`).")
        return

    ui.print_section("Step 1/5 — Model Role Guide")
    ui.print_status_table(
        "Roles",
        [
            ("planner", "Decompose large plans into micro-tasks"),
            ("coder", "Implement tasks and edit files"),
            ("reviewer", "Approve/reject with quality checks"),
            ("embedder", "Power semantic file retrieval"),
        ],
    )

    ui.print_section("Step 2/5 — Environment")
    lm_url = ui.ask_text("LM Studio URL", "http://127.0.0.1:1234")
    ram_gb = ui.ask_float("Total RAM/VRAM (GB)", 36.0)
    tier = "small" if ram_gb < 24 else "medium" if ram_gb < 64 else "large"

    profiles = _default_model_profiles()
    picked = dict(profiles[tier])
    ui.print_section("Step 3/5 — Model Profile")
    ui.print_note(f"Suggested profile for {ram_gb:.0f} GB: {tier}")
    if not ui.ask_yes_no("Use suggested model keys?", True):
        ui.print_section("Step 4/5 — Manual Model Keys")
        ui.print_info("Enter LM Studio model keys (`lms ls` can list keys).")
        for role in ("planner", "coder", "reviewer", "embedder"):
            picked[role] = ui.ask_text(f"{role} model", picked[role])

    config = _build_config_from_inputs(
        lm_studio_base_url=lm_url,
        total_ram_gb=ram_gb,
        model_keys=picked,
    )
    ui.print_section("Step 5/5 — Final Review")
    ui.print_info("Confirming values before writing `factory.yaml`.")
    ui.print_status_table(
        "Setup Summary",
        [
            ("LM Studio URL", lm_url),
            ("Capacity Tier", tier),
            ("Planner", picked["planner"]),
            ("Coder", picked["coder"]),
            ("Reviewer", picked["reviewer"]),
            ("Embedder", picked["embedder"]),
        ],
    )
    _write_yaml(cwd / "factory.yaml", config)
    ui.print_info("Wrote factory.yaml.")

    if ui.ask_yes_no("Create a starter plan now?", True):
        title = ui.ask_text("Plan filename (without .md)", "InitialSetup")
        prompt = ui.ask_text("One-line goal", "Set up project skeleton and first feature")
        plan_path = cwd / "plans" / f"{title}.md"
        if not plan_path.exists():
            plan_path.write_text(
                f"# {title}\n\n## Goal\n{prompt}\n\n## Notes\n- Expand this plan before running.\n",
                encoding="utf-8",
            )
            ui.print_info(f"Created starter plan: {plan_path}")
        else:
            ui.print_info(f"Plan already exists: {plan_path}")

    ui.print_note("Workspace is ready.")
    _post_action_prompt(cwd, cwd / "factory.yaml", default="health")


def _configure_models_interactive(cwd: Path, cfg_path: Path | None) -> int:
    cfg = cfg_path or (cwd / "factory.yaml")
    if not cfg.is_file():
        print("No factory.yaml found. Run `lao init` first.")
        return 1

    with open(cfg, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("models", {})

    init_settings(config_path=cfg, cwd=cwd)
    from local_ai_agent_orchestrator.model_manager import ModelManager

    mm = ModelManager()
    if not mm.health_check():
        ui.print_note("LM Studio is unreachable. Start server and run again.")
        return 1

    available = mm.get_available_models()
    ui.print_header("Configure Models", "Update role-to-model mappings from available LM Studio models.")
    ui.print_status_table(
        f"Available Models ({len(available)})",
        [(str(i + 1), m) for i, m in enumerate(available)],
    )

    ui.print_section("Role Mapping")
    ui.print_info("Press Enter to keep a role unchanged.")
    changed: list[str] = []
    for role in ("planner", "coder", "reviewer", "embedder"):
        role_cfg = (data.get("models") or {}).get(role) or {}
        cur = role_cfg.get("key", "")
        new_key = ui.ask_text(f"{role} model key", cur)
        if new_key:
            role_cfg["key"] = new_key
        if new_key and new_key != cur:
            changed.append(role)
        data["models"][role] = role_cfg

    _write_yaml(cfg, data)
    ui.print_note(f"Updated model mappings in {cfg}.")
    ui.print_status_table(
        "Roles Updated",
        [(r, "changed") for r in changed] or [("none", "no changes")],
    )
    _post_action_prompt(cwd, cfg, default="run")
    return 0


def _home_menu(cwd: Path, cfg_path: Path | None) -> int:
    ui.print_header(
        "Interactive Home",
        "Environment status and guided next actions.\n"
        "LAO is a local planner-coder-reviewer orchestration system for long-running coding workflows.\n"
        "Website: https://lao.keyhan.info",
    )
    cfg = cfg_path or (cwd / "factory.yaml")
    has_config = cfg.is_file()

    lm_ok = False
    missing: list[str] = []
    plans_count = len(list((cwd / "plans").glob("*.md"))) if (cwd / "plans").exists() else 0

    if has_config:
        init_settings(config_path=cfg, cwd=cwd)
        from local_ai_agent_orchestrator.model_manager import ModelManager

        mm = ModelManager()
        lm_ok = mm.health_check()
        if lm_ok:
            missing = mm.verify_models_exist()
    else:
        pass

    ui.print_status_table(
        "Environment",
        [
            ("Config", f"{'OK' if has_config else 'MISSING'} ({cfg})"),
            ("LM Studio", "OK" if lm_ok else ("UNREACHABLE" if has_config else "skipped (no config)")),
            ("Models", "OK" if (has_config and lm_ok and not missing) else (f"MISSING {len(missing)}" if missing else "skipped")),
            ("Plans", f"{plans_count} file(s) in ./plans"),
        ],
    )
    if missing:
        ui.print_note("Configured models missing from LM Studio:")
        for m in missing:
            ui.print_info(f"- {m}")
        ui.print_info("Tip: choose 'configure model names' to remap quickly.")

    choice = ui.ask_choice(
        "Choose Action",
        [
            ("1", "init workspace"),
            ("2", "run orchestrator"),
            ("3", "health check"),
            ("4", "configure model names"),
            ("5", "quit"),
        ],
        "1" if not has_config else "2",
    )
    return int(choice) if choice.isdigit() else 5


def _post_action_prompt(cwd: Path, config_path: Path, default: str = "run") -> None:
    choice = ui.ask_choice(
        "Next Action",
        [
            ("health", "Run `lao health` now"),
            ("run", "Run `lao run` now"),
            ("exit", "Exit"),
        ],
        default,
    )
    if choice == "health":
        init_settings(config_path=config_path, cwd=cwd)
        from local_ai_agent_orchestrator import runner
        from local_ai_agent_orchestrator.model_manager import ModelManager

        runner.health_check(ModelManager())
    elif choice == "run":
        init_settings(config_path=config_path, cwd=cwd)
        from local_ai_agent_orchestrator import runner

        runner.run_entry(plan=None, single_run=False, use_tui=ui.is_tty())


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    cwd = Path.cwd()

    parser = argparse.ArgumentParser(
        prog="lao",
        description="Local AI Agent Orchestrator -- LM Studio multi-agent coding pipeline",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to factory.yaml (default: ./factory.yaml if present)",
    )
    parser.add_argument(
        "--lm-studio-url",
        dest="lm_studio_base",
        default=os.getenv("LM_STUDIO_BASE_URL"),
        help="LM Studio base URL (overrides config / env)",
    )
    parser.add_argument(
        "--ram-gb",
        dest="total_ram_gb",
        type=float,
        default=None,
        help="Total system RAM in GB (logged / future tuning)",
    )
    parser.add_argument("--workspace", type=Path, default=None, help="Workspace root path")
    parser.add_argument("--plans-dir", type=Path, default=None, help="Plans directory")
    parser.add_argument("--db", type=Path, dest="db_path", default=None, help="SQLite database path")
    parser.add_argument("--planner-model", dest="planner_model", default=None)
    parser.add_argument("--coder-model", dest="coder_model", default=None)
    parser.add_argument("--reviewer-model", dest="reviewer_model", default=None)
    parser.add_argument("--embedder-model", dest="embedder_model", default=None)
    parser.add_argument(
        "--plan",
        type=str,
        default=None,
        help="Process a specific plan file (with run)",
    )
    parser.add_argument(
        "--single-run",
        action="store_true",
        help="Process queue once then exit",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Classic scrolling log (no full-screen dashboard)",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Disable per-plan Git snapshots and phase commits (overrides factory.yaml)",
    )
    parser.add_argument("--phase-gated", action="store_true", help="Enable role-batched phase execution")
    parser.add_argument("--batch-size", type=int, default=None, help="Coder batch size per wave")
    parser.add_argument(
        "--max-context-utilization",
        type=float,
        default=None,
        help="Planner target context utilization ratio (0-1)",
    )
    parser.add_argument(
        "--quality-gate",
        type=str,
        default=None,
        choices=["strict", "standard", "off"],
        help="Quality gate strictness",
    )
    parser.add_argument(
        "--plan-phase",
        type=str,
        default=None,
        help="Execute only tasks belonging to a named phase",
    )
    parser.add_argument(
        "--architect-only",
        action="store_true",
        help="Run architect decomposition only and stop before coding/review",
    )

    sub = parser.add_subparsers(dest="command", help="Command")

    sub.add_parser("run", help="Run orchestrator")
    preflight_p = sub.add_parser("preflight", help="Run plan context preflight diagnostics")
    preflight_p.add_argument(
        "--plan",
        type=str,
        required=True,
        help="Plan file path (or file name inside plans directory)",
    )

    sub.add_parser("status", help="Show task queue status")
    sub.add_parser("benchmark", help="Run core reliability benchmark scenarios")
    sub.add_parser("kpi", help="Generate KPI snapshot for weekly tracking")
    sub.add_parser("dashboard", help="Generate operator dashboard snapshot")
    sub.add_parser("health", help="Check LM Studio and models")
    sub.add_parser("retry-failed", help="Retry failed tasks by resetting them to pending")
    sub.add_parser("reset-failed", help="Deprecated alias for retry-failed")
    sub.add_parser("configure-models", help="Interactively update model keys in factory.yaml")
    init_p = sub.add_parser("init", help="Scaffold factory.example.yaml, .lao/, plans/")
    init_p.add_argument(
        "--skip-readme",
        action="store_true",
        help="Do not create README.md if missing",
    )
    init_p.add_argument(
        "--no-interactive",
        action="store_true",
        help="Skip welcome banner (for scripts)",
    )

    args = parser.parse_args(argv)

    try:
        cfg_path = _resolve_config_path(cwd, args.config)

        if args.command is None and sys.stdout.isatty():
            action = _home_menu(cwd, cfg_path)
            if action == 1:
                _run_init(cwd, skip_readme=False, no_interactive=False)
                return
            if action == 3:
                args.command = "health"
            elif action == 4:
                args.command = "configure-models"
            elif action == 5:
                return
            else:
                args.command = "run"

        if args.command == "init":
            _run_init(cwd, skip_readme=args.skip_readme, no_interactive=args.no_interactive)
            return

        model_keys = {}
        if args.planner_model:
            model_keys["planner"] = args.planner_model
        if args.coder_model:
            model_keys["coder"] = args.coder_model
        if args.reviewer_model:
            model_keys["reviewer"] = args.reviewer_model
        if args.embedder_model:
            model_keys["embedder"] = args.embedder_model

        overrides = {}
        if args.lm_studio_base:
            overrides["lm_studio_base"] = args.lm_studio_base
        if args.total_ram_gb is not None:
            overrides["total_ram_gb"] = args.total_ram_gb
        if args.workspace:
            overrides["workspace_root"] = args.workspace
        if args.plans_dir:
            overrides["plans_dir"] = args.plans_dir
        if args.db_path:
            overrides["db_path"] = args.db_path
        if args.no_git:
            overrides["git_enabled"] = False
        if args.phase_gated:
            overrides["phase_gated"] = True
        if args.batch_size is not None:
            overrides["coder_batch_size"] = args.batch_size
        if args.max_context_utilization is not None:
            overrides["max_context_utilization"] = args.max_context_utilization
        if args.quality_gate is not None:
            overrides["quality_gate_mode"] = args.quality_gate
        if args.plan_phase is not None:
            overrides["execution_phase"] = args.plan_phase
        if args.architect_only:
            overrides["architect_only"] = True

        if args.command == "configure-models":
            raise SystemExit(_configure_models_interactive(cwd, cfg_path))

        init_settings(
            config_path=cfg_path,
            cwd=cwd,
            model_key_overrides=model_keys or None,
            **overrides,
        )

        from local_ai_agent_orchestrator import runner

        cmd = args.command or "run"

        if cmd == "status":
            runner.print_status(TaskQueue())
            return
        if cmd == "preflight":
            ok = runner.preflight_plan(args.plan)
            if not ok:
                raise SystemExit(1)
            return

        if cmd == "health":
            from local_ai_agent_orchestrator.model_manager import ModelManager

            runner.health_check(ModelManager())
            return
        if cmd == "benchmark":
            from local_ai_agent_orchestrator.history import append_history_entry
            from local_ai_agent_orchestrator.benchmarks import (
                run_benchmark_suite,
                write_benchmark_report,
            )
            from local_ai_agent_orchestrator.settings import get_settings
            import json

            ws = get_settings().config_dir
            prev = None
            hist_path = ws / "benchmark_history.json"
            if hist_path.exists():
                try:
                    rows = json.loads(hist_path.read_text(encoding="utf-8"))
                    if isinstance(rows, list) and rows:
                        prev = rows[-1]
                except Exception:
                    prev = None
            payload = run_benchmark_suite(previous=prev)
            out = write_benchmark_report(ws, payload)
            hist = append_history_entry(ws, "benchmark_history.json", payload)
            print(f"Benchmarks: {payload['passed']}/{payload['total']} passed")
            print(f"Pass rate: {payload.get('pass_rate', 0):.2%}")
            print(f"Report: {out}")
            print(f"History: {hist}")
            if not bool(payload.get("gate", {}).get("gate_passed", False)):
                raise SystemExit(2)
            return
        if cmd == "kpi":
            from local_ai_agent_orchestrator.history import append_history_entry
            from local_ai_agent_orchestrator.kpi import build_kpi_snapshot, write_kpi_snapshot
            from local_ai_agent_orchestrator.settings import get_settings

            q = TaskQueue()
            payload = build_kpi_snapshot(q)
            ws = get_settings().config_dir
            out = write_kpi_snapshot(ws, payload)
            hist = append_history_entry(ws, "kpi_history.json", payload)
            print("KPI snapshot generated.")
            print(f"Report: {out}")
            print(f"History: {hist}")
            return
        if cmd == "dashboard":
            from local_ai_agent_orchestrator.history import append_history_entry
            from local_ai_agent_orchestrator.dashboards import (
                build_dashboard_snapshot,
                write_dashboard_snapshot,
            )
            from local_ai_agent_orchestrator.settings import get_settings

            q = TaskQueue()
            payload = build_dashboard_snapshot(q)
            ws = get_settings().config_dir
            out = write_dashboard_snapshot(ws, payload)
            hist = append_history_entry(ws, "dashboard_history.json", payload)
            print("Dashboard snapshot generated.")
            print(f"Report: {out}")
            print(f"History: {hist}")
            return

        if cmd in ("retry-failed", "reset-failed"):
            q = TaskQueue()
            reset_count = q.reset_failed_tasks()
            print(f"Reset {reset_count} failed tasks to pending.")
            return

        if cmd in (None, "run"):
            use_tui = sys.stdout.isatty() and not args.plain
            ok = runner.run_entry(
                plan=args.plan,
                single_run=args.single_run,
                use_tui=use_tui,
            )
            if ok is False:
                raise SystemExit(1)
            return

        parser.print_help()
    except KeyboardInterrupt:
        ui.print_goodbye(resume_command="lao run")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
