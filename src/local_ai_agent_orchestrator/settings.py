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
    # Determinism knobs (per role).
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    seed: Optional[int] = None
    repetition_penalty: Optional[float] = None
    supports_json_schema: bool = False


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
            key="deepseek-r1-distill-qwen-32b",
            context_length=8192,
            max_completion=2048,
            supports_tools=False,
            size_bytes=18_500_000_000,
            description="Reviewer (DeepSeek R1 Distill Qwen 32B)",
        ),
        "embedder": ModelConfig(
            key="text-embedding-nomic-embed-text-v1.5",
            context_length=2048,
            max_completion=0,
            supports_tools=False,
            size_bytes=84_106_624,
            description="Embedding model for semantic file search",
        ),
        "pilot": ModelConfig(
            key="qwen_qwen3.5-35b-a3b",
            context_length=32768,
            max_completion=16384,
            supports_tools=True,
            size_bytes=21_513_639_040,
            description="Interactive pilot / command agent",
        ),
        "analyst": ModelConfig(
            key="qwen2.5-7b-instruct",
            context_length=65536,
            max_completion=8192,
            supports_tools=False,
            size_bytes=4_500_000_000,
            description="Read-only project analyst (large context, small weights)",
        ),
    }


@dataclass
class GitSettings:
    """Per-plan Git snapshots and phase commits (see docs/CONFIGURATION.md)."""

    enabled: bool = True
    plan_file_name: str = "LAO_PLAN.md"
    commit_trailers: bool = False
    worktrees: bool = False  # speculative coder retries inside `git worktree add`


@dataclass
class HooksSettings:
    """Optional ``hooks.py`` discovery for pre/post tool & phase callbacks."""

    enabled: bool = True
    path: Optional[str] = None  # defaults to <config_dir>/hooks.py


@dataclass
class OTelSettings:
    """Optional OpenTelemetry exporter (lazy-loaded)."""

    enabled: bool = False
    endpoint: Optional[str] = None
    service_name: str = "lao"


@dataclass
class Settings:
    lm_studio_base: str = "http://127.0.0.1:1234"
    openai_api_key: str = "lm-studio"
    # Directory containing factory.yaml (or cwd if no config file).
    config_dir: Path = field(default_factory=Path.cwd)
    # Fallback when no per-plan workspace is active (rare); override via paths.workspace in YAML.
    workspace_root: Path = field(default_factory=lambda: Path.cwd() / ".lao" / "_misc")
    plans_dir: Path = field(default_factory=lambda: Path.cwd() / "plans")
    db_path: Path = field(default_factory=lambda: Path.cwd() / ".lao" / "state.db")
    total_ram_gb: Optional[float] = None

    models: dict[str, ModelConfig] = field(default_factory=_default_models)

    model_load_timeout_s: int = 180
    model_load_poll_interval_s: int = 3
    max_task_attempts: int = 3
    plan_watch_interval_s: int = 10
    llm_request_timeout_s: int = 300
    llm_retry_attempts: int = 3
    llm_retry_backoff_base_s: int = 5
    phase_gated: bool = True
    coder_batch_size: int = 4
    reviewer_batch_size: int = 6
    max_context_utilization: float = 0.85
    quality_gate_mode: str = "standard"
    validation_build_cmd: Optional[str] = None
    validation_lint_cmd: Optional[str] = None
    infer_validation_commands: bool = True
    validation_profile: str = "default"
    validation_profiles: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "default": {
                "commands": [],
                "block_on_severities": ["critical", "major"],
                "block_min_confidence": 0.6,
                "block_min_confidence_by_analyzer_kind": {},
                "block_min_confidence_by_analyzer_id": {},
            }
        }
    )
    placeholder_max_markers_per_kloc: float = 3.0
    placeholder_max_ratio: float = 0.02
    preflight_reserved_tokens: int = 256
    execution_phase: Optional[str] = None
    strict_adherence: bool = False
    strict_closure_allowed_statuses: list[str] = field(
        default_factory=lambda: ["validated"]
    )
    retry_cooldown_base_s: int = 30
    retry_cap_coder: int = 3
    retry_cap_reviewer: int = 3
    retry_cap_validation: int = 3
    no_progress_repeat_limit: int = 2
    benchmark_min_pass_rate: float = 0.85
    benchmark_fail_on_regression: bool = True
    architect_only: bool = False
    pilot_mode_enabled: bool = True
    pilot_context_lines: int = 50
    analyst_enabled: bool = True

    memory_release_fraction: float = 0.75
    swap_growth_limit_mb: float = 512.0
    memory_settle_timeout_s: int = 60
    memory_poll_interval_s: int = 2

    git: GitSettings = field(default_factory=GitSettings)
    hooks: HooksSettings = field(default_factory=HooksSettings)
    otel: OTelSettings = field(default_factory=OTelSettings)

    # New tunables (Tier 1-3 features).
    permissions: dict[str, Any] = field(
        default_factory=lambda: {"mode": "auto", "allow": [], "deny": []}
    )
    skills_dirs: list[str] = field(default_factory=list)
    skills_enabled: bool = True
    memory_enabled: bool = True
    memory_user_path: Optional[str] = None  # default: ~/.lao/MEMORY.md
    memory_project_filename: str = "LAO_MEMORY.md"
    output_style: str = "narrative"  # terse | narrative | json
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    verifier_enabled: bool = True
    compaction_enabled: bool = True
    compaction_keep_recent: int = 8

    # End-to-end agentic v1 ─────────────────────────────────────────
    contract_author_enabled: bool = True
    inner_repair_max_iterations: int = 3
    inner_repair_token_budget: int = 6000
    critic_quorum_enabled: bool = False
    critic_quorum_size: int = 3
    critic_models: list[str] = field(default_factory=list)
    critic_keep_reviewer_vote: bool = True
    spec_doctor_enabled: bool = True
    decision_log_enabled: bool = True
    plan_integrator_enabled: bool = True

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
    if config_path and config_path.is_file():
        config_dir = config_path.resolve().parent
    else:
        config_dir = cwd.resolve()

    base = Settings(
        config_dir=config_dir,
        workspace_root=config_dir / ".lao" / "_misc",
        plans_dir=config_dir / "plans",
        db_path=config_dir / ".lao" / "state.db",
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
            "pilot": "pilot",
            "analyst": "analyst",
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
        if k == "git_enabled" and v is not None:
            base = replace(base, git=replace(base.git, enabled=bool(v)))
            continue
        if k in path_keys and v is not None:
            base = replace(base, **{k: Path(v).expanduser().resolve()})
        elif hasattr(base, k) and k not in ("models", "git"):
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
            phase_gated=bool(orch.get("phase_gated", base.phase_gated)),
            coder_batch_size=int(orch.get("coder_batch_size", base.coder_batch_size)),
            reviewer_batch_size=int(orch.get("reviewer_batch_size", base.reviewer_batch_size)),
            max_context_utilization=float(
                orch.get("max_context_utilization", base.max_context_utilization)
            ),
            quality_gate_mode=str(orch.get("quality_gate_mode", base.quality_gate_mode)),
            validation_build_cmd=(
                str(orch["validation_build_cmd"])
                if orch.get("validation_build_cmd") is not None
                else base.validation_build_cmd
            ),
            validation_lint_cmd=(
                str(orch["validation_lint_cmd"])
                if orch.get("validation_lint_cmd") is not None
                else base.validation_lint_cmd
            ),
            infer_validation_commands=bool(
                orch.get("infer_validation_commands", base.infer_validation_commands)
            ),
            validation_profile=str(orch.get("validation_profile", base.validation_profile)),
            placeholder_max_markers_per_kloc=float(
                orch.get(
                    "placeholder_max_markers_per_kloc",
                    base.placeholder_max_markers_per_kloc,
                )
            ),
            placeholder_max_ratio=float(
                orch.get("placeholder_max_ratio", base.placeholder_max_ratio)
            ),
            preflight_reserved_tokens=int(
                orch.get("preflight_reserved_tokens", base.preflight_reserved_tokens)
            ),
            strict_adherence=bool(orch.get("strict_adherence", base.strict_adherence)),
            strict_closure_allowed_statuses=[
                str(x).strip().lower()
                for x in (
                    orch.get(
                        "strict_closure_allowed_statuses",
                        base.strict_closure_allowed_statuses,
                    )
                    or base.strict_closure_allowed_statuses
                )
                if str(x).strip()
            ],
            retry_cooldown_base_s=int(
                orch.get("retry_cooldown_base_s", base.retry_cooldown_base_s)
            ),
            retry_cap_coder=int(orch.get("retry_cap_coder", base.retry_cap_coder)),
            retry_cap_reviewer=int(orch.get("retry_cap_reviewer", base.retry_cap_reviewer)),
            retry_cap_validation=int(orch.get("retry_cap_validation", base.retry_cap_validation)),
            no_progress_repeat_limit=int(
                orch.get("no_progress_repeat_limit", base.no_progress_repeat_limit)
            ),
            benchmark_min_pass_rate=float(
                orch.get("benchmark_min_pass_rate", base.benchmark_min_pass_rate)
            ),
            benchmark_fail_on_regression=bool(
                orch.get("benchmark_fail_on_regression", base.benchmark_fail_on_regression)
            ),
            pilot_mode_enabled=bool(orch.get("pilot_mode_enabled", base.pilot_mode_enabled)),
            pilot_context_lines=int(orch.get("pilot_context_lines", base.pilot_context_lines)),
            analyst_enabled=bool(orch.get("analyst_enabled", base.analyst_enabled)),
        )
        if isinstance(orch.get("validation_profiles"), dict):
            profiles = {
                str(k): v
                for k, v in (orch.get("validation_profiles") or {}).items()
                if isinstance(v, dict)
            }
            if profiles:
                base = replace(base, validation_profiles=profiles)

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
                temperature=(
                    float(spec["temperature"]) if spec.get("temperature") is not None else cur.temperature
                ),
                top_p=(float(spec["top_p"]) if spec.get("top_p") is not None else cur.top_p),
                seed=(int(spec["seed"]) if spec.get("seed") is not None else cur.seed),
                repetition_penalty=(
                    float(spec["repetition_penalty"])
                    if spec.get("repetition_penalty") is not None
                    else cur.repetition_penalty
                ),
                supports_json_schema=bool(
                    spec.get("supports_json_schema", cur.supports_json_schema)
                ),
            )
        base = replace(base, models=merged)

    git_cfg = data.get("git") or {}
    if git_cfg:
        g = base.git
        base = replace(
            base,
            git=GitSettings(
                enabled=bool(git_cfg.get("enabled", g.enabled)),
                plan_file_name=str(git_cfg.get("plan_file_name", g.plan_file_name)),
                commit_trailers=bool(git_cfg.get("commit_trailers", g.commit_trailers)),
                worktrees=bool(git_cfg.get("worktrees", g.worktrees)),
            ),
        )

    # ── New tunables (Tier 1-3) ─────────────────────────────────────
    perms = data.get("permissions")
    if isinstance(perms, dict):
        base = replace(
            base,
            permissions={
                "mode": str(perms.get("mode", "auto")).strip().lower() or "auto",
                "allow": [str(x) for x in (perms.get("allow") or []) if str(x).strip()],
                "deny": [str(x) for x in (perms.get("deny") or []) if str(x).strip()],
            },
        )

    skills_cfg = data.get("skills")
    if isinstance(skills_cfg, dict):
        base = replace(
            base,
            skills_enabled=bool(skills_cfg.get("enabled", base.skills_enabled)),
            skills_dirs=[
                str((yaml_root / d).resolve()) if not Path(d).is_absolute() else str(Path(d))
                for d in (skills_cfg.get("dirs") or [])
                if str(d).strip()
            ],
        )

    mem_cfg = data.get("memory")
    if isinstance(mem_cfg, dict):
        base = replace(
            base,
            memory_enabled=bool(mem_cfg.get("enabled", base.memory_enabled)),
            memory_user_path=(
                str(mem_cfg["user_path"]) if mem_cfg.get("user_path") else base.memory_user_path
            ),
            memory_project_filename=str(
                mem_cfg.get("project_filename", base.memory_project_filename)
            ),
        )

    if "output_style" in data:
        base = replace(base, output_style=str(data["output_style"]).strip().lower() or "narrative")

    mcp_cfg = data.get("mcp_servers")
    if isinstance(mcp_cfg, list):
        servers: list[dict[str, Any]] = []
        for entry in mcp_cfg:
            if isinstance(entry, dict) and entry.get("name"):
                servers.append({k: v for k, v in entry.items() if v is not None})
        base = replace(base, mcp_servers=servers)

    hooks_cfg = data.get("hooks")
    if isinstance(hooks_cfg, dict):
        base = replace(
            base,
            hooks=HooksSettings(
                enabled=bool(hooks_cfg.get("enabled", base.hooks.enabled)),
                path=(str(hooks_cfg["path"]) if hooks_cfg.get("path") else base.hooks.path),
            ),
        )

    otel_cfg = data.get("otel")
    if isinstance(otel_cfg, dict):
        base = replace(
            base,
            otel=OTelSettings(
                enabled=bool(otel_cfg.get("enabled", base.otel.enabled)),
                endpoint=(
                    str(otel_cfg["endpoint"]) if otel_cfg.get("endpoint") else base.otel.endpoint
                ),
                service_name=str(otel_cfg.get("service_name", base.otel.service_name)),
            ),
        )

    agentic_cfg = data.get("agentic") or {}
    if isinstance(agentic_cfg, dict):
        base = replace(
            base,
            contract_author_enabled=bool(
                agentic_cfg.get("contract_author_enabled", base.contract_author_enabled)
            ),
            inner_repair_max_iterations=int(
                agentic_cfg.get("inner_repair_max_iterations", base.inner_repair_max_iterations)
            ),
            inner_repair_token_budget=int(
                agentic_cfg.get("inner_repair_token_budget", base.inner_repair_token_budget)
            ),
            critic_quorum_enabled=bool(
                agentic_cfg.get("critic_quorum_enabled", base.critic_quorum_enabled)
            ),
            critic_quorum_size=max(
                1, int(agentic_cfg.get("critic_quorum_size", base.critic_quorum_size))
            ),
            critic_models=[
                str(x).strip()
                for x in (agentic_cfg.get("critic_models") or base.critic_models)
                if str(x).strip()
            ],
            critic_keep_reviewer_vote=bool(
                agentic_cfg.get("critic_keep_reviewer_vote", base.critic_keep_reviewer_vote)
            ),
            spec_doctor_enabled=bool(
                agentic_cfg.get("spec_doctor_enabled", base.spec_doctor_enabled)
            ),
            decision_log_enabled=bool(
                agentic_cfg.get("decision_log_enabled", base.decision_log_enabled)
            ),
            plan_integrator_enabled=bool(
                agentic_cfg.get("plan_integrator_enabled", base.plan_integrator_enabled)
            ),
        )

    orch_extra = data.get("orchestration") or {}
    if isinstance(orch_extra.get("verifier_enabled"), bool):
        base = replace(base, verifier_enabled=bool(orch_extra["verifier_enabled"]))
    if isinstance(orch_extra.get("compaction_enabled"), bool):
        base = replace(base, compaction_enabled=bool(orch_extra["compaction_enabled"]))
    if (
        "compaction_keep_recent" in orch_extra
        and orch_extra["compaction_keep_recent"] is not None
    ):
        try:
            base = replace(
                base, compaction_keep_recent=int(orch_extra["compaction_keep_recent"])
            )
        except (TypeError, ValueError):
            pass

    return base


def reset_settings_for_tests():
    global _settings
    _settings = None
