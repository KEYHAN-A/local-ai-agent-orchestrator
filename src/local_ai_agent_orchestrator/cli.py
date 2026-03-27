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

from local_ai_agent_orchestrator import __version__
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
            "pilot_mode_enabled": True,
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
            "pilot": {
                "key": "qwen_qwen3.5-35b-a3b",
                "context_length": 32768,
                "max_completion": 16384,
                "supports_tools": True,
                "size_bytes": 21513639040,
                "description": "Interactive pilot / command agent",
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
            "pilot": "qwen2.5-7b-instruct",
        },
        "medium": {
            "planner": "qwen_qwen3.5-35b-a3b",
            "coder": "qwen/qwen3-coder-30b",
            "reviewer": "deepseek-r1-distill-qwen-32b",
            "embedder": "text-embedding-nomic-embed-text-v1.5",
            "pilot": "qwen_qwen3.5-35b-a3b",
        },
        "large": {
            "planner": "qwen_qwen3.5-35b-a3b",
            "coder": "qwen/qwen3-coder-30b",
            "reviewer": "deepseek/deepseek-r1",
            "embedder": "text-embedding-nomic-embed-text-v1.5",
            "pilot": "qwen_qwen3.5-35b-a3b",
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
            "pilot_mode_enabled": True,
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
            "pilot": {
                "key": model_keys.get("pilot", model_keys["planner"]),
                "context_length": 32768,
                "max_completion": 16384,
                "supports_tools": True,
                "size_bytes": 21513639040,
                "description": "Interactive pilot / command agent",
            },
        },
    }


def _resolve_config_path(cwd: Path, cli_config: Path | None) -> Path | None:
    cfg_path = cli_config or _default_config_path(cwd)
    env_config = os.getenv("LAO_CONFIG") or os.getenv("FACTORY_CONFIG")
    if env_config and Path(env_config).is_file():
        cfg_path = Path(env_config)
    return cfg_path


def _is_filesystem_root(cwd: Path) -> bool:
    resolved = cwd.resolve()
    return resolved == Path(resolved.anchor)


def _is_home_root(cwd: Path) -> bool:
    try:
        return cwd.resolve() == Path.home().resolve()
    except Exception:
        return False


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
            ("pilot", "Interactive chat agent when pipeline is idle"),
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
        for role in ("planner", "coder", "reviewer", "embedder", "pilot"):
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
            ("Pilot", picked["pilot"]),
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
    for role in ("planner", "coder", "reviewer", "embedder", "pilot"):
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


def _home_menu(cwd: Path, cfg_path: Path | None) -> str:
    show_root_warning = _is_home_root(cwd)
    ui.print_splash(
        tagline=(
            "Pilot chat for planning, debugging, tool runs, and queue control; "
            "then hand off to planner -> coder -> reviewer autopilot.  "
            "https://lao.keyhan.info"
        ),
    )
    if show_root_warning:
        ui.print_warning(
            "You are running LAO from your home directory root. "
            "For safer and cleaner workflows, use a project folder or subdirectory."
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

    # Discover registered projects for the project picker
    from local_ai_agent_orchestrator.project_registry import ProjectRegistry

    registry = ProjectRegistry()
    known_projects = registry.list_all()

    models_ok = has_config and lm_ok and not missing
    summary_bits = [
        f"config {'ok' if has_config else 'missing'}",
        f"LM Studio {'ok' if lm_ok else 'down' if has_config else '—'}",
        f"models {'ok' if models_ok else (f'{len(missing)} missing' if missing else '—')}",
        f"{plans_count} plan(s)",
    ]
    if known_projects:
        summary_bits.append(f"{len(known_projects)} registered project(s)")
    ui.print_info(" · ".join(summary_bits))
    ui.print_info("Guide: /help for pilot commands, /resume to return to autopilot from chat.")
    ui.print_status_table(
        "Environment",
        [
            ("Config", f"{'OK' if has_config else 'MISSING'} ({cfg})"),
            ("LM Studio", "OK" if lm_ok else ("UNREACHABLE" if has_config else "skipped (no config)")),
            ("Models", "OK" if models_ok else (f"MISSING {len(missing)}" if missing else "skipped")),
            ("Plans", f"{plans_count} file(s) in ./plans"),
        ],
    )
    if missing:
        ui.print_note("Configured models missing from LM Studio:")
        for m in missing:
            ui.print_info(f"- {m}")
        ui.print_info("Tip: choose 'configure model names' to remap quickly.")

    # Show known projects table when available
    if known_projects:
        project_rows = []
        for p in known_projects:
            p = registry.refresh(p)
            status_parts = []
            if p.pending_tasks:
                status_parts.append(f"{p.pending_tasks} pending")
            if p.failed_tasks:
                status_parts.append(f"{p.failed_tasks} failed")
            status_str = ", ".join(status_parts) if status_parts else "idle"
            project_rows.append((p.name, f"{status_str}  {p.path}"))
        ui.print_status_table("Known Projects", project_rows)

    if show_root_warning:
        default_id = "exit"
    elif not has_config and not known_projects:
        default_id = "init"
    elif not has_config and known_projects:
        default_id = "projects"
    elif not lm_ok:
        default_id = "health"
    elif missing:
        default_id = "configure-models"
    else:
        default_id = "pilot"

    menu_choices: list[tuple[str, str]] = [
        (
            "pilot",
            "Pilot — chat with local LLM, run tools, inspect status, and resume pipeline",
        ),
        ("run", "Run orchestrator (watch plans, process queue, auto-enter pilot when idle)"),
    ]
    if not has_config:
        menu_choices.append(
            ("scan", "Scan for LAO projects in subdirectories"),
        )
    menu_choices.extend([
        ("projects", "Manage registered LAO projects"),
        ("init", "Initialize workspace (factory.yaml, plans/, .lao/)"),
        ("health", "Health check (LM Studio server, model mappings, guardrails)"),
        ("configure-models", "Configure role -> model name mappings"),
        ("exit", "Exit"),
    ])
    return ui.select_option(
        "Choose action  (↑↓ move · Enter select · Ctrl+C cancel)",
        menu_choices,
        default_id,
    )


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


def _run_projects_command(
    action: str,
    target: str | None,
    root: Path,
    tags: list[str],
) -> None:
    """Handle ``lao projects`` sub-actions."""
    from local_ai_agent_orchestrator.project_registry import ProjectRegistry

    reg = ProjectRegistry()

    if action == "list":
        entries = reg.list_all()
        if not entries:
            print("No projects registered. Run `lao projects scan` to discover LAO projects.")
            return
        print(f"Registered projects ({len(entries)}):\n")
        for e in entries:
            e = reg.refresh(e)
            parts = []
            if e.has_config:
                parts.append("config")
            if e.plans_count:
                parts.append(f"{e.plans_count} plans")
            if e.pending_tasks:
                parts.append(f"{e.pending_tasks} pending")
            if e.failed_tasks:
                parts.append(f"{e.failed_tasks} failed")
            status = ", ".join(parts) if parts else "empty"
            print(f"  {e.name:30s}  [{status}]  {e.path}")
        return

    if action == "scan":
        print(f"Scanning {root} for LAO projects...")
        found = reg.scan(root)
        if found:
            print(f"\nFound {len(found)} project(s):")
            for e in found:
                print(f"  {e.name:30s}  {e.path}")
        else:
            print("No LAO projects found.")
        return

    if action == "add":
        if not target:
            print("Usage: lao projects add <path> [--tag TAG]")
            return
        entry = reg.add(Path(target), tags=tags or None)
        print(f"Registered project: {entry.name} ({entry.path})")
        return

    if action == "remove":
        if not target:
            print("Usage: lao projects remove <name-or-path>")
            return
        if reg.remove(target):
            print(f"Removed: {target}")
        else:
            print(f"Not found: {target}")
        return

    if action == "use":
        if not target:
            print("Usage: lao projects use <name-or-path>")
            return
        entry = reg.get(target)
        if not entry:
            p = Path(target).expanduser()
            if p.is_dir():
                entry = reg.add(p)
            else:
                print(f"Project not found: {target}")
                return
        project_path = Path(entry.path)
        config_file = project_path / "factory.yaml"
        if not config_file.exists():
            config_file = project_path / "factory.yml"
        if config_file.exists():
            init_settings(config_path=config_file, cwd=project_path)
        else:
            init_settings(cwd=project_path)
        print(f"Active project: {entry.name} ({entry.path})")
        return

    if action == "needs-action":
        urgent = reg.needs_action()
        if not urgent:
            print("All projects are up to date.")
            return
        print(f"Projects needing action ({len(urgent)}):\n")
        for e in urgent:
            parts = []
            if e.pending_tasks:
                parts.append(f"{e.pending_tasks} pending")
            if e.failed_tasks:
                parts.append(f"{e.failed_tasks} failed")
            status = ", ".join(parts) if parts else "stale"
            print(f"  {e.name:30s}  [{status}]  {e.path}")
        return

    print(f"Unknown projects action: {action}")


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    cwd = Path.cwd()

    parser = argparse.ArgumentParser(
        prog="lao",
        description=(
            "LAO: local planner-coder-reviewer orchestration for long-running coding workflows."
        ),
        epilog=(
            "Examples:\n"
            "  lao\n"
            "  lao run --single-run\n"
            "  lao init --no-interactive\n"
            "  lao health\n"
            "  lao --version"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    global_opts = parser.add_argument_group("Global options")
    global_opts.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    global_opts.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to factory.yaml (default: ./factory.yaml if present)",
    )
    global_opts.add_argument(
        "--lm-studio-url",
        dest="lm_studio_base",
        default=os.getenv("LM_STUDIO_BASE_URL"),
        help="LM Studio base URL (overrides config / env)",
    )
    global_opts.add_argument(
        "--ram-gb",
        dest="total_ram_gb",
        type=float,
        default=None,
        help="Total system RAM in GB (logged / future tuning)",
    )
    global_opts.add_argument("--workspace", type=Path, default=None, help="Workspace root path")
    global_opts.add_argument("--plans-dir", type=Path, default=None, help="Plans directory")
    global_opts.add_argument(
        "--db", type=Path, dest="db_path", default=None, help="SQLite database path"
    )
    global_opts.add_argument("--planner-model", dest="planner_model", default=None)
    global_opts.add_argument("--coder-model", dest="coder_model", default=None)
    global_opts.add_argument("--reviewer-model", dest="reviewer_model", default=None)
    global_opts.add_argument("--embedder-model", dest="embedder_model", default=None)
    global_opts.add_argument("--pilot-model", dest="pilot_model", default=None)
    global_opts.add_argument(
        "--plan",
        type=str,
        default=None,
        help="Process a specific plan file (with run)",
    )
    run_opts = parser.add_argument_group("Run behavior options")
    run_opts.add_argument(
        "--single-run",
        action="store_true",
        help="Process queue once then exit",
    )
    run_opts.add_argument(
        "--plain",
        action="store_true",
        help="Classic scrolling log (no full-screen dashboard)",
    )
    run_opts.add_argument(
        "--no-git",
        action="store_true",
        help="Disable per-plan Git snapshots and phase commits (overrides factory.yaml)",
    )
    run_opts.add_argument(
        "--phase-gated", action="store_true", help="Enable role-batched phase execution"
    )
    run_opts.add_argument("--batch-size", type=int, default=None, help="Coder batch size per wave")
    run_opts.add_argument(
        "--max-context-utilization",
        type=float,
        default=None,
        help="Planner target context utilization ratio (0-1)",
    )
    run_opts.add_argument(
        "--quality-gate",
        type=str,
        default=None,
        choices=["strict", "standard", "off"],
        help="Quality gate strictness",
    )
    run_opts.add_argument(
        "--plan-phase",
        type=str,
        default=None,
        help="Execute only tasks belonging to a named phase",
    )
    run_opts.add_argument(
        "--architect-only",
        action="store_true",
        help="Run architect decomposition only and stop before coding/review",
    )
    run_opts.add_argument(
        "--no-pilot",
        action="store_true",
        help="Disable Pilot Mode (keep legacy watch behavior when idle)",
    )
    run_opts.add_argument(
        "--pilot-only",
        action="store_true",
        help="Enter Pilot Mode immediately without running the pipeline",
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
    report_p = sub.add_parser("report", help="Quality report schema operations")
    report_p.add_argument(
        "action",
        choices=["check", "migrate"],
        help="Check schema metadata or migrate report to current schema",
    )
    report_p.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Path to quality report JSON (default: <config_dir>/quality_report.json)",
    )
    sub.add_parser("pilot", help="Enter Pilot Mode (interactive chat agent)")
    projects_p = sub.add_parser("projects", help="Manage registered LAO projects")
    projects_p.add_argument(
        "projects_action",
        nargs="?",
        default="list",
        choices=["list", "scan", "add", "use", "remove", "needs-action"],
        help="Projects sub-action (default: list)",
    )
    projects_p.add_argument(
        "projects_target",
        nargs="?",
        default=None,
        help="Path or name argument for add/use/remove",
    )
    projects_p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Root directory for scan (default: cwd)",
    )
    projects_p.add_argument(
        "--tag",
        action="append",
        dest="tags",
        default=[],
        help="Tag(s) for the project (with add)",
    )
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
        if _is_filesystem_root(cwd):
            print(
                "Warning: running `lao` from filesystem root is not recommended. "
                "Please run it inside a project folder or subdirectory.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if _is_home_root(cwd) and args.command is not None:
            print(
                "Warning: running `lao` from your home directory root is not recommended. "
                "Consider using a project folder or subdirectory.",
                file=sys.stderr,
            )

        cfg_path = _resolve_config_path(cwd, args.config)

        if args.command is None and sys.stdout.isatty():
            action = _home_menu(cwd, cfg_path)
            if action == "init":
                _run_init(cwd, skip_readme=False, no_interactive=False)
                return
            if action == "scan":
                _run_projects_command("scan", None, cwd, [])
                return
            if action == "projects":
                _run_projects_command("list", None, cwd, [])
                return
            if action == "health":
                args.command = "health"
            elif action == "configure-models":
                args.command = "configure-models"
            elif action == "pilot":
                args.command = "pilot"
            elif action == "exit":
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
        if args.pilot_model:
            model_keys["pilot"] = args.pilot_model

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
        if args.no_pilot:
            overrides["pilot_mode_enabled"] = False

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
                reasons = payload.get("gate", {}).get("gate_reasons", [])
                if isinstance(reasons, list) and reasons:
                    print("Gate reasons:")
                    for reason in reasons:
                        print(f"- {reason}")
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
            import json

            q = TaskQueue()
            ws = get_settings().config_dir
            prev = None
            hist_path = ws / "dashboard_history.json"
            if hist_path.exists():
                try:
                    rows = json.loads(hist_path.read_text(encoding="utf-8"))
                    if isinstance(rows, list) and rows:
                        prev = rows[-1]
                except Exception:
                    prev = None
            payload = build_dashboard_snapshot(q, previous=prev)
            out = write_dashboard_snapshot(ws, payload)
            hist = append_history_entry(ws, "dashboard_history.json", payload)
            print("Dashboard snapshot generated.")
            print(f"Report: {out}")
            print(f"History: {hist}")
            return
        if cmd == "report":
            from local_ai_agent_orchestrator.report_schema import (
                check_quality_report_schema,
                load_and_migrate_quality_report,
            )
            from local_ai_agent_orchestrator.settings import get_settings

            ws = get_settings().config_dir
            target = args.file or (ws / "quality_report.json")
            if not target.exists():
                print(f"Report not found: {target}")
                raise SystemExit(1)
            if args.action == "check":
                result = check_quality_report_schema(target)
                print(f"Schema check: {'OK' if result.get('ok') else 'FAIL'}")
                print(f"Path: {target}")
                print(f"Reason: {result.get('reason')}")
                if result.get("report_meta"):
                    print(f"Report meta: {result.get('report_meta')}")
                if not bool(result.get("ok")):
                    raise SystemExit(2)
                return
            migrated = load_and_migrate_quality_report(target, write_back=True)
            print(f"Migrated report schema in place: {target}")
            print(f"Report meta: {migrated.get('report_meta')}")
            return

        if cmd == "projects":
            _run_projects_command(
                args.projects_action,
                args.projects_target,
                args.root or cwd,
                args.tags,
            )
            return

        if cmd in ("retry-failed", "reset-failed"):
            q = TaskQueue()
            reset_count = q.reset_failed_tasks()
            print(f"Reset {reset_count} failed tasks to pending.")
            return

        if cmd == "pilot" or args.pilot_only:
            use_tui = sys.stdout.isatty() and not args.plain
            from local_ai_agent_orchestrator.model_manager import ModelManager
            from local_ai_agent_orchestrator.pilot import PilotResult
            from local_ai_agent_orchestrator.settings import get_settings as _gs

            mm = ModelManager()
            if not mm.health_check():
                print("LM Studio server is not reachable.", file=sys.stderr)
                raise SystemExit(1)
            q = TaskQueue()
            # Ensure fallback workspace exists so Pilot tools like list_dir('.') work
            # even when running from an uninitialized directory.
            _s = _gs()
            (_s.config_dir / ".lao").mkdir(parents=True, exist_ok=True)
            _s.workspace_root.mkdir(parents=True, exist_ok=True)
            _s.plans_dir.mkdir(parents=True, exist_ok=True)

            if use_tui:
                from local_ai_agent_orchestrator.unified_ui import UnifiedUI
                from local_ai_agent_orchestrator.runner import _run_pilot_with_unified_ui

                unified = UnifiedUI(history_path=_s.config_dir / ".lao" / "chat_history")
                unified.set_queue_getter(lambda: q)
                unified.start()
                try:
                    result = _run_pilot_with_unified_ui(mm, q, unified)
                finally:
                    unified.stop()
            else:
                from local_ai_agent_orchestrator.pilot_ui import enter_pilot_mode
                result = enter_pilot_mode(mm, q, use_tui=False)

            if result == PilotResult.RESUME_PIPELINE:
                ok = runner.run_entry(plan=None, single_run=False, use_tui=use_tui)
                if ok is False:
                    raise SystemExit(1)
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
