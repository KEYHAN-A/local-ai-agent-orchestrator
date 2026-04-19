# SPDX-License-Identifier: GPL-3.0-or-later
"""
Minimal MCP client.

The Model Context Protocol (MCP) is a JSON-RPC 2.0 protocol over stdio. To
keep LAO dependency-free we ship a tiny synchronous transport instead of
pulling in the official ``mcp`` SDK -- when the SDK *is* installed we will
prefer it.

Servers declared under ``factory.yaml: mcp_servers`` are discovered and their
tools are registered into the global ``Tool`` registry as
``mcp__<server>__<tool>`` so the rest of LAO can dispatch them through the
existing permission + audit pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.tools.base import (
    Tool,
    parameters_schema,
    register,
)

log = logging.getLogger(__name__)


@dataclass
class _MCPServer:
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    proc: Optional[subprocess.Popen] = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        try:
            env = {**os.environ, **self.env}
            self.proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=self.cwd,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            log.warning(f"[MCP] cannot start {self.name}: {e}")
            return False
        return True

    def request(self, method: str, params: Optional[dict] = None) -> dict:
        if self.proc is None or self.proc.poll() is not None:
            if not self.start():
                return {"error": {"message": "server not running"}}
        assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None
        msg = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params or {},
        }
        with self.lock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        try:
            return json.loads(line) if line else {"error": {"message": "no response"}}
        except json.JSONDecodeError as e:
            return {"error": {"message": f"json decode: {e}"}}


_SERVERS: dict[str, _MCPServer] = {}
_REGISTERED_TOOLS: list[str] = []


def _build_call(server: _MCPServer, tool_name: str):
    def _invoke(**kwargs: Any) -> str:
        resp = server.request("tools/call", {"name": tool_name, "arguments": kwargs})
        if "error" in resp and resp["error"]:
            return f"ERROR: {resp['error'].get('message', 'unknown')}"
        result = resp.get("result")
        if isinstance(result, dict) and "content" in result:
            parts = result["content"]
            if isinstance(parts, list):
                return "\n".join(str(p.get("text") or p) for p in parts)
        return str(result)
    return _invoke


def discover_and_register() -> list[str]:
    """Start configured MCP servers and register their tools.

    Returns the list of newly-registered tool names. Failures are logged and
    skipped so a misconfigured server never blocks LAO startup.
    """
    try:
        s = get_settings()
    except RuntimeError:
        return []
    new_tools: list[str] = []
    for entry in s.mcp_servers or []:
        name = str(entry.get("name") or "").strip()
        cmd = entry.get("command") or []
        if not name or not cmd:
            continue
        if name in _SERVERS:
            continue
        server = _MCPServer(
            name=name,
            command=[str(c) for c in cmd],
            env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
            cwd=str(entry["cwd"]) if entry.get("cwd") else None,
        )
        if not server.start():
            continue
        _SERVERS[name] = server
        listing = server.request("tools/list")
        tools = (listing.get("result") or {}).get("tools") or []
        for t in tools:
            tname = str(t.get("name") or "").strip()
            if not tname:
                continue
            full_name = f"mcp__{name}__{tname}"
            schema = t.get("inputSchema") or parameters_schema({})
            tool = Tool(
                name=full_name,
                description=str(t.get("description") or f"MCP tool {tname} on {name}"),
                parameters=schema if isinstance(schema, dict) else parameters_schema({}),
                call=_build_call(server, tname),
                is_read_only=False,
                is_concurrency_safe=False,
                plan_mode_safe=False,
            )
            register(tool)
            new_tools.append(full_name)
    if new_tools:
        log.info(f"[MCP] registered {len(new_tools)} tool(s): {new_tools}")
        _REGISTERED_TOOLS.extend(new_tools)
    return new_tools


def shutdown() -> None:
    for server in _SERVERS.values():
        try:
            if server.proc is not None and server.proc.poll() is None:
                server.proc.terminate()
        except Exception:
            pass
    _SERVERS.clear()
