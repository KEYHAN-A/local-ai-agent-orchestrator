# SPDX-License-Identifier: GPL-3.0-or-later
"""
Command-line interface for Local AI Agent Orchestrator.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

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
            "workspace": "./workspace",
            "plans": "./plans",
            "database": "./state.db",
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
        },
        "models": {
            "planner": {
                "key": "qwen_qwen3.5-35b-a3b",
                "context_length": 16384,
                "max_completion": 4096,
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
                "key": "deepseek-r1-distill-qwen-32b-mlx",
                "context_length": 8192,
                "max_completion": 2048,
                "supports_tools": False,
                "size_bytes": 26633743197,
                "description": "Reviewer",
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

    sub = parser.add_subparsers(dest="command", help="Command")

    sub.add_parser("run", help="Run orchestrator (default if no subcommand)")

    sub.add_parser("status", help="Show task queue status")
    sub.add_parser("health", help="Check LM Studio and models")
    sub.add_parser("reset-failed", help="Reset failed tasks to pending")
    sub.add_parser("init", help="Write factory.example.yaml in current directory")

    args = parser.parse_args(argv)

    if args.command == "init":
        _write_example_config(cwd / "factory.example.yaml")
        (cwd / "workspace").mkdir(exist_ok=True)
        (cwd / "plans").mkdir(exist_ok=True)
        print("Created workspace/ and plans/ if missing.")
        print("Copy factory.example.yaml to factory.yaml and edit model keys for your machine.")
        return

    cfg_path = args.config
    if cfg_path is None:
        cfg_path = _default_config_path(cwd)

    env_config = os.getenv("LAO_CONFIG") or os.getenv("FACTORY_CONFIG")
    if env_config and Path(env_config).is_file():
        cfg_path = Path(env_config)

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

    if cmd == "health":
        from local_ai_agent_orchestrator.model_manager import ModelManager

        runner.health_check(ModelManager())
        return

    if cmd == "reset-failed":
        q = TaskQueue()
        cur = q._conn.execute(
            "UPDATE micro_tasks SET status='pending', attempt=0 WHERE status='failed'"
        )
        print(f"Reset {cur.rowcount} failed tasks to pending.")
        return

    if cmd in (None, "run"):
        runner.run_entry(
            plan=args.plan,
            single_run=args.single_run,
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
