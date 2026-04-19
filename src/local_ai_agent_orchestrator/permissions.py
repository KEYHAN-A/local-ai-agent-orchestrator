# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tool permission system.

Inspired by Claude Code's ``src/hooks/toolPermission/``: every tool dispatch
passes through :func:`evaluate` which combines (a) the tool's own
:meth:`Tool.check_permissions` callback, (b) the per-mode default policy, and
(c) wildcard rules sourced from :mod:`settings`.

Modes
-----

``auto``
    Allow everything; the historical LAO behavior.
``confirm``
    Allow read-only tools; mutating tools require an interactive ``approve``
    callback (used by the pilot UI).
``plan_only``
    Allow only read-only / plan-mode-safe tools; mutating tools are denied.
``bypass``
    Equivalent to ``auto`` (kept for parity / future extension).

Rule patterns
-------------

Rules look like ``Tool(<glob>)``. The argument glob is matched against a
canonical "rule key" produced per tool (``shell_exec`` uses the command,
``file_*`` uses the path, etc.). Two lists are supported under
``settings.permissions``::

    permissions:
      mode: auto
      allow:
        - Bash(git *)
        - FileRead(*)
      deny:
        - Bash(rm -rf *)
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.tools.base import PermissionDecision, Tool

log = logging.getLogger(__name__)


# Map external rule names (Claude Code style) -> internal tool names.
_RULE_NAME_ALIASES = {
    "Bash": "shell_exec",
    "Shell": "shell_exec",
    "FileRead": "file_read",
    "FileWrite": "file_write",
    "FileEdit": "file_patch",
    "FilePatch": "file_patch",
    "ListDir": "list_dir",
    "TodoWrite": "task_todo_set",
}


@dataclass
class PermissionConfig:
    mode: str = "auto"
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls) -> "PermissionConfig":
        try:
            s = get_settings()
        except RuntimeError:
            return cls()
        raw = getattr(s, "permissions", None) or {}
        if isinstance(raw, dict):
            mode = str(raw.get("mode", "auto")).strip().lower() or "auto"
            allow = [str(x) for x in (raw.get("allow") or []) if str(x).strip()]
            deny = [str(x) for x in (raw.get("deny") or []) if str(x).strip()]
            return cls(mode=mode, allow=allow, deny=deny)
        return cls()


def _canonical_key(tool_name: str, args: dict) -> str:
    """Stable key matched against rule globs."""
    if tool_name == "shell_exec":
        return str(args.get("command", "")).strip()
    if tool_name in {"file_read", "file_write", "file_patch", "list_dir"}:
        return str(args.get("path", "")).strip()
    if tool_name == "task_todo_set":
        return str(len(args.get("items") or []))
    return ""


def _parse_rule(rule: str) -> tuple[str, str] | None:
    rule = rule.strip()
    if not rule:
        return None
    if "(" in rule and rule.endswith(")"):
        head, body = rule.split("(", 1)
        body = body[:-1]
        tool_name = _RULE_NAME_ALIASES.get(head.strip(), head.strip())
        return (tool_name, body.strip())
    tool_name = _RULE_NAME_ALIASES.get(rule, rule)
    return (tool_name, "*")


def _matches(rules: list[str], tool_name: str, key: str) -> bool:
    for raw in rules:
        parsed = _parse_rule(raw)
        if not parsed:
            continue
        rname, pattern = parsed
        if rname != tool_name:
            continue
        if pattern in {"", "*"}:
            return True
        if fnmatch.fnmatch(key, pattern):
            return True
    return False


# ── Approval bridge (set by the pilot UI when mode == 'confirm') ──────


_APPROVAL_HOOK: Optional[Callable[[str, dict, str], bool]] = None


def set_approval_hook(hook: Optional[Callable[[str, dict, str], bool]]) -> None:
    global _APPROVAL_HOOK
    _APPROVAL_HOOK = hook


def _ask_approval(tool: Tool, args: dict, prompt: str) -> bool:
    if _APPROVAL_HOOK is None:
        return False
    try:
        return bool(_APPROVAL_HOOK(tool.name, args, prompt))
    except Exception as e:
        log.warning(f"[Permissions] approval hook raised: {e}")
        return False


# ── Public API ────────────────────────────────────────────────────────


def evaluate(tool: Tool, args: dict, *, mode: Optional[str] = None) -> PermissionDecision:
    """Decide whether *tool* may run with *args* under the active mode."""
    cfg = PermissionConfig.from_settings()
    effective_mode = (mode or cfg.mode or "auto").strip().lower() or "auto"
    key = _canonical_key(tool.name, args)

    # 1. Per-tool callback wins (used for plan-mode hard blocks).
    if tool.check_permissions is not None:
        try:
            decision = tool.check_permissions(args)
        except Exception as e:
            log.warning(f"[Permissions] {tool.name} check_permissions raised: {e}")
            decision = None
        if isinstance(decision, PermissionDecision) and not decision.granted:
            return decision

    # 2. Explicit deny rules (always honored).
    if _matches(cfg.deny, tool.name, key):
        return PermissionDecision.deny(
            reason="deny_rule",
            prompt=f"Tool {tool.name}({key!r}) blocked by deny rule.",
        )

    # 3. Explicit allow rules trump everything below.
    if _matches(cfg.allow, tool.name, key):
        return PermissionDecision.allow(reason="allow_rule")

    # 4. Per-mode defaults.
    if effective_mode in {"auto", "bypass"}:
        return PermissionDecision.allow(reason=f"mode_{effective_mode}")
    if effective_mode == "plan_only":
        if tool.is_read_only or tool.plan_mode_safe:
            return PermissionDecision.allow(reason="plan_only_safe")
        return PermissionDecision.deny(
            reason="plan_only_blocks_mutations",
            prompt=f"plan_only mode blocks {tool.name}.",
        )
    if effective_mode == "confirm":
        if tool.is_read_only:
            return PermissionDecision.allow(reason="read_only_auto_allow")
        if _ask_approval(tool, args, prompt=f"Allow {tool.name}({key})?"):
            return PermissionDecision.allow(reason="user_approved")
        return PermissionDecision.deny(
            reason="user_denied",
            prompt=f"User denied {tool.name}({key}).",
        )

    # Unknown mode -> safe default
    return PermissionDecision.deny(reason=f"unknown_mode:{effective_mode}")
