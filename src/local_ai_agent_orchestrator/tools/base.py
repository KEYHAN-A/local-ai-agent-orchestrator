# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tool contract: every workspace tool is a self-contained ``Tool`` value.

Inspired by Claude Code's per-tool contract (``Tool.ts``): each Tool carries
its own input schema, permission model, read-only / concurrency markers, an
optional system-prompt contribution and renderers for terminal output.

The OpenAI ``tools=[...]`` schema and the ``TOOL_DISPATCH`` map used by the
phases / pilot loops are derived deterministically from the registered Tool
instances -- no dual maintenance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

log = logging.getLogger(__name__)


# ── JSON-schema parameter helpers ─────────────────────────────────────


def param(
    type_: str,
    description: str = "",
    *,
    enum: Optional[list[Any]] = None,
    default: Any = None,
    items: Optional[dict] = None,
) -> dict:
    """Build a JSON-schema property entry for a tool parameter."""
    p: dict = {"type": type_}
    if description:
        p["description"] = description
    if enum is not None:
        p["enum"] = list(enum)
    if default is not None:
        p["default"] = default
    if items is not None:
        p["items"] = items
    return p


def parameters_schema(
    properties: Mapping[str, dict],
    required: Optional[list[str]] = None,
) -> dict:
    """Wrap a dict of property schemas in a JSON-schema object."""
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required or []),
    }


# ── Permission decision objects ───────────────────────────────────────


@dataclass(frozen=True)
class PermissionDecision:
    granted: bool
    reason: str = ""
    prompt: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> "PermissionDecision":
        return cls(granted=True, reason=reason)

    @classmethod
    def deny(cls, reason: str, prompt: str = "") -> "PermissionDecision":
        return cls(granted=False, reason=reason, prompt=prompt)


# ── Tool dataclass ────────────────────────────────────────────────────


@dataclass
class Tool:
    """A single workspace tool registered with the orchestrator.

    ``call`` is a plain Python callable invoked with keyword arguments after
    schema validation. ``check_permissions`` may return ``None`` to defer to
    the global permission system, or an explicit :class:`PermissionDecision`.
    """

    name: str
    description: str
    parameters: dict
    call: Callable[..., Any]
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    prompt_contribution: str = ""
    aliases: tuple[str, ...] = ()
    check_permissions: Optional[Callable[[dict], Optional[PermissionDecision]]] = None
    format_invocation: Optional[Callable[[dict], str]] = None
    format_result: Optional[Callable[[Any], str]] = None
    plan_mode_safe: bool = False  # True when allowed during plan mode

    def to_openai_schema(self) -> dict:
        """Render as an OpenAI function-calling tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def validate(self, raw_args: Any) -> dict:
        """Lightweight schema validation: required keys + JSON-array shape.

        Avoids adding a pydantic dependency; we only enforce the few invariants
        the dispatcher needs to call the underlying function safely.
        """
        if raw_args is None:
            args: dict = {}
        elif isinstance(raw_args, dict):
            args = dict(raw_args)
        else:
            raise ValueError(
                f"tool {self.name!r}: expected JSON object args, got {type(raw_args).__name__}"
            )
        required = self.parameters.get("required") or []
        missing = [k for k in required if k not in args or args[k] in (None, "")]
        if missing:
            raise ValueError(
                f"tool {self.name!r}: missing required arg(s): {', '.join(missing)}"
            )
        return args


# ── Registry ──────────────────────────────────────────────────────────


_REGISTRY: dict[str, Tool] = {}
_ORDER: list[str] = []


def register(tool: Tool) -> Tool:
    """Register a Tool. Last registration wins (used by tests / hot reload)."""
    if tool.name not in _REGISTRY:
        _ORDER.append(tool.name)
    _REGISTRY[tool.name] = tool
    for alias in tool.aliases:
        _REGISTRY[alias] = tool
    return tool


def get(name: str) -> Optional[Tool]:
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    seen: set[str] = set()
    out: list[Tool] = []
    for n in _ORDER:
        t = _REGISTRY.get(n)
        if t is None or t.name in seen:
            continue
        seen.add(t.name)
        out.append(t)
    return out


def reset_registry() -> None:
    _REGISTRY.clear()
    _ORDER.clear()


def build_openai_schemas(names: Optional[list[str]] = None) -> list[dict]:
    """Return ``tools=[...]`` payload for the Chat Completions API."""
    pool = (
        [_REGISTRY[n] for n in names if n in _REGISTRY]
        if names is not None
        else all_tools()
    )
    return [t.to_openai_schema() for t in pool]


def build_dispatch(names: Optional[list[str]] = None) -> dict[str, Callable[..., Any]]:
    pool = (
        [(n, _REGISTRY[n]) for n in names if n in _REGISTRY]
        if names is not None
        else [(t.name, t) for t in all_tools()]
    )
    return {name: tool.call for name, tool in pool}


# ── Safe JSON helpers used by tool implementations ────────────────────


def safe_json_dumps(payload: Any, *, max_chars: int = 4000) -> str:
    try:
        out = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception as e:
        out = f"<<json_dump_error: {e}>>"
    if len(out) > max_chars:
        return out[:max_chars] + "...<truncated>"
    return out
