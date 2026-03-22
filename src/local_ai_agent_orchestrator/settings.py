# SPDX-License-Identifier: GPL-3.0-or-later
"""
Runtime settings for Local AI Agent Orchestrator.

Loaded from factory.yaml + environment variables + CLI overrides.
Call init_settings() before get_settings() is used.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

import yaml

# ── Defaults (used when YAML / env omit values) ───────────────────────


@dataclass(frozen=True)
class ModelConfig:
    key: str
    context_length: int
    max_completion: int
    supports_tools: bool
    size_bytes: int
    description: str


def _default_models() -> dict[str, ModelConfig]:
    return {
        "planner": ModelConfig(
            key="qwen_qwen3.5-35b-a3b",
            context_length=32768,
            max_completion=16384,
            supports_tools=True,
            size_bytes=21_513_639_040,
            description="Planner / architect model",
        ),
        "coder": ModelConfig(
            key="qwen/qwen3-coder-30b",
            context_length=16384,
            max_completion=4096,
            supports_tools=True,
            size_bytes=17_190_972_664,
            description="Coder model",
        ),
        "reviewer": ModelConfig(
            key="deepseek-r1-distill-qwen-32b-mlx",
            context_length=8192,
            max_completion=2048,
            supports_tools=False,
            size_bytes=26_633_743_197,
            description="Reviewer model",
        ),
        "embedder": ModelConfig(
            key="text-embedding-nomic-embed-text-v1.5",
            context_length=2048,
            max_completion=0,
            supports_tools=False,
            size_bytes=84_106_624,
            description="Embedding model for semantic file search",
        ),
    }


@dataclass
class Settings:
    lm_studio_base: str = "http://127.0.0.1:1234"
    openai_api_key: str = "lm-studio"
    workspace_root: Path = field(default_factory=lambda: Path.cwd() / "workspace")
    plans_dir: Path = field(default_factory=lambda: Path.cwd() / "plans")
    db_path: Path = field(default_factory=lambda: Path.cwd() / "state.db")
    total_ram_gb: Optional[float] = None

    models: dict[str, ModelConfig] = field(default_factory=_default_models)

    model_load_timeout_s: int = 180
    model_load_poll_interval_s: int = 3
    max_task_attempts: int = 3
    plan_watch_interval_s: int = 10
    llm_request_timeout_s: int = 300
    llm_retry_attempts: int = 3
    llm_retry_backoff_base_s: int = 5

    memory_release_fraction: float = 0.75
    swap_growth_limit_mb: float = 512.0
    memory_settle_timeout_s: int = 60
    memory_poll_interval_s: int = 2

    @property
    def openai_base_url(self) -> str:
        return f"{self.lm_studio_base.rstrip('/')}/v1"


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    if _settings is None:
        raise RuntimeError("Settings not initialized; call init_settings() first.")
    return _settings


def init_settings(
    *,
    config_path: Optional[Path] = None,
    cwd: Optional[Path] = None,
    model_key_overrides: Optional[dict[str, str]] = None,
    **overrides: Any,
) -> Settings:
    """
    Build Settings from optional YAML file, environment, and keyword overrides.

    Paths in YAML are resolved relative to the YAML file's parent directory.
    """
    global _settings
    cwd = cwd or Path.cwd()
    base = Settings(
        workspace_root=cwd / "workspace",
        plans_dir=cwd / "plans",
        db_path=cwd / "state.db",
    )

    # Environment
    if v := os.getenv("LM_STUDIO_BASE_URL"):
        base = replace(base, lm_studio_base=v)
    if v := os.getenv("OPENAI_API_KEY"):
        base = replace(base, openai_api_key=v)
    if v := os.getenv("WORKSPACE_ROOT"):
        base = replace(base, workspace_root=Path(v).expanduser().resolve())
    if v := os.getenv("PLANS_DIR"):
        base = replace(base, plans_dir=Path(v).expanduser().resolve())
    if v := os.getenv("DB_PATH"):
        base = replace(base, db_path=Path(v).expanduser().resolve())
    if v := os.getenv("TOTAL_RAM_GB"):
        try:
            base = replace(base, total_ram_gb=float(v))
        except ValueError:
            pass

    yaml_root: Optional[Path] = None
    if config_path and config_path.is_file():
        yaml_root = config_path.resolve().parent
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        base = _merge_yaml(base, data, yaml_root)

    # Per-role model keys from CLI (planner_model -> planner, etc.)
    if model_key_overrides:
        models = dict(base.models)
        role_map = {
            "planner": "planner",
            "coder": "coder",
            "reviewer": "reviewer",
            "embedder": "embedder",
        }
        for cli_name, role in role_map.items():
            new_key = model_key_overrides.get(cli_name)
            if new_key and role in models:
                cur = models[role]
                models[role] = replace(cur, key=new_key)
        base = replace(base, models=models)

    # CLI / explicit overrides (Path-typed fields)
    path_keys = {"workspace_root", "plans_dir", "db_path"}
    for k, v in overrides.items():
        if v is None or k == "model_key_overrides":
            continue
        if k in path_keys and v is not None:
            base = replace(base, **{k: Path(v).expanduser().resolve()})
        elif hasattr(base, k) and k not in ("models",):
            base = replace(base, **{k: v})

    _settings = base
    return base


def _merge_yaml(base: Settings, data: dict[str, Any], yaml_root: Path) -> Settings:
    if "lm_studio_base_url" in data:
        base = replace(base, lm_studio_base=str(data["lm_studio_base_url"]))
    if "openai_api_key" in data:
        base = replace(base, openai_api_key=str(data["openai_api_key"]))
    if "total_ram_gb" in data and data["total_ram_gb"] is not None:
        base = replace(base, total_ram_gb=float(data["total_ram_gb"]))

    paths = data.get("paths") or {}
    if "workspace" in paths:
        base = replace(
            base, workspace_root=(yaml_root / paths["workspace"]).resolve()
        )
    if "plans" in paths:
        base = replace(base, plans_dir=(yaml_root / paths["plans"]).resolve())
    if "database" in paths:
        base = replace(base, db_path=(yaml_root / paths["database"]).resolve())

    mg = data.get("memory_gate") or {}
    if mg:
        base = replace(
            base,
            memory_release_fraction=float(mg.get("release_fraction", base.memory_release_fraction)),
            swap_growth_limit_mb=float(mg.get("swap_growth_limit_mb", base.swap_growth_limit_mb)),
            memory_settle_timeout_s=int(mg.get("settle_timeout_s", base.memory_settle_timeout_s)),
            memory_poll_interval_s=int(mg.get("poll_interval_s", base.memory_poll_interval_s)),
        )

    orch = data.get("orchestration") or {}
    if orch:
        base = replace(
            base,
            model_load_timeout_s=int(orch.get("model_load_timeout_s", base.model_load_timeout_s)),
            model_load_poll_interval_s=int(
                orch.get("model_load_poll_interval_s", base.model_load_poll_interval_s)
            ),
            max_task_attempts=int(orch.get("max_task_attempts", base.max_task_attempts)),
            plan_watch_interval_s=int(orch.get("plan_watch_interval_s", base.plan_watch_interval_s)),
            llm_request_timeout_s=int(orch.get("llm_request_timeout_s", base.llm_request_timeout_s)),
            llm_retry_attempts=int(orch.get("llm_retry_attempts", base.llm_retry_attempts)),
            llm_retry_backoff_base_s=int(
                orch.get("llm_retry_backoff_base_s", base.llm_retry_backoff_base_s)
            ),
        )

    models_data = data.get("models")
    if models_data:
        merged = dict(base.models)
        for role, spec in models_data.items():
            if role not in merged:
                continue
            cur = merged[role]
            merged[role] = ModelConfig(
                key=str(spec.get("key", cur.key)),
                context_length=int(spec.get("context_length", cur.context_length)),
                max_completion=int(spec.get("max_completion", cur.max_completion)),
                supports_tools=bool(spec.get("supports_tools", cur.supports_tools)),
                size_bytes=int(spec.get("size_bytes", cur.size_bytes)),
                description=str(spec.get("description", cur.description)),
            )
        base = replace(base, models=merged)

    return base


def reset_settings_for_tests():
    global _settings
    _settings = None
