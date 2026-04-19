# SPDX-License-Identifier: GPL-3.0-or-later
"""
Optional hooks framework.

Loads ``<config_dir>/hooks.py`` (or the path configured under
``settings.hooks.path``) once per process and exposes safe wrappers for the
runtime to fire ``pre_tool``, ``post_tool``, ``pre_phase`` and ``post_phase``
events. All hook functions are optional; missing or raising hooks are logged
and ignored so the orchestrator never crashes because of user code.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from local_ai_agent_orchestrator.settings import get_settings

log = logging.getLogger(__name__)


_LOADED: Optional[object] = None
_LOADED_PATH: Optional[Path] = None


def _hooks_path() -> Optional[Path]:
    try:
        s = get_settings()
    except RuntimeError:
        return None
    if not s.hooks.enabled:
        return None
    if s.hooks.path:
        return Path(s.hooks.path).expanduser().resolve()
    return (s.config_dir / "hooks.py").resolve()


def reload(force: bool = False) -> Optional[object]:
    global _LOADED, _LOADED_PATH
    path = _hooks_path()
    if path is None or not path.is_file():
        _LOADED, _LOADED_PATH = None, None
        return None
    if not force and _LOADED is not None and _LOADED_PATH == path:
        return _LOADED
    spec = importlib.util.spec_from_file_location("lao_user_hooks", str(path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        log.warning(f"[Hooks] failed to load {path}: {e}")
        return None
    _LOADED = module
    _LOADED_PATH = path
    log.info(f"[Hooks] loaded {path}")
    return module


def _call(name: str, *args: Any, **kwargs: Any) -> Any:
    mod = reload()
    if mod is None:
        return None
    fn: Optional[Callable[..., Any]] = getattr(mod, name, None)
    if fn is None or not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log.warning(f"[Hooks] {name} raised: {e}")
        return None


def pre_tool(tool_name: str, args: dict, ctx: Optional[dict] = None) -> None:
    _call("pre_tool", tool_name, args, ctx or {})


def post_tool(tool_name: str, args: dict, result: Any, ctx: Optional[dict] = None) -> None:
    _call("post_tool", tool_name, args, result, ctx or {})


def pre_phase(phase: str, plan_id: Optional[str], ctx: Optional[dict] = None) -> None:
    _call("pre_phase", phase, plan_id, ctx or {})


def post_phase(phase: str, plan_id: Optional[str], ctx: Optional[dict] = None) -> None:
    _call("post_phase", phase, plan_id, ctx or {})
