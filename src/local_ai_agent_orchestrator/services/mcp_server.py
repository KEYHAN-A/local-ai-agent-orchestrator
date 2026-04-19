# SPDX-License-Identifier: GPL-3.0-or-later
"""
Lightweight MCP server exposing LAO pipeline tools over stdio.

External clients (Cursor, Claude Desktop, VS Code MCP) can spawn ``lao
mcp-server`` and drive LAO through the standard JSON-RPC 2.0 ``tools/list``
and ``tools/call`` methods.

The server is intentionally minimal -- enough to interoperate with stock MCP
clients but without bringing in the full SDK surface.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Iterable

from local_ai_agent_orchestrator.tools import base as tool_base

log = logging.getLogger(__name__)


_PROTOCOL_VERSION = "2024-11-05"


def _exposed_tools() -> Iterable[tool_base.Tool]:
    """Tools we surface to external clients.

    Read-only / pipeline-introspection tools are surfaced first so generic
    clients can browse the project without risking mutations.
    """
    safe_first = [
        "list_dir",
        "file_read",
        "shell_exec",
        "find_relevant_files",
    ]
    pool = tool_base.all_tools()
    by_name = {t.name: t for t in pool}
    out = [by_name[n] for n in safe_first if n in by_name]
    extras = [t for t in pool if t.name not in safe_first]
    return out + extras


def _handle(req: dict[str, Any]) -> dict[str, Any]:
    method = req.get("method", "")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": _PROTOCOL_VERSION,
                "serverInfo": {"name": "lao", "version": "3.1"},
                "capabilities": {"tools": {"listChanged": False}},
            },
        }
    if method == "tools/list":
        tools = []
        for t in _exposed_tools():
            tools.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.parameters,
                }
            )
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        tool = tool_base.get(name)
        if tool is None:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"Unknown tool {name!r}"},
            }
        try:
            validated = tool.validate(args)
            result = tool.call(**validated)
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32000, "message": str(e)},
            }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": str(result)}]},
        }
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve(stream_in=sys.stdin, stream_out=sys.stdout) -> int:
    """Run the MCP server until EOF; returns the exit code."""
    log.info("[MCP-Server] starting on stdio")
    while True:
        line = stream_in.readline()
        if not line:
            return 0
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle(req)
        stream_out.write(json.dumps(resp) + "\n")
        stream_out.flush()
