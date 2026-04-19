# SPDX-License-Identifier: GPL-3.0-or-later
"""
Bounded ephemeral sub-agent (``agent_run``).

The pilot can delegate a focused investigation to a sub-agent that runs with a
trimmed tool whitelist (read-only by default), its own short context, and a
hard cap on rounds / tokens. The sub-agent returns a single string result; its
inner conversation never bleeds into the parent context.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from local_ai_agent_orchestrator.tools.base import (
    Tool,
    param,
    parameters_schema,
    register,
)

log = logging.getLogger(__name__)


_DEFAULT_READONLY = ("file_read", "list_dir", "find_relevant_files", "shell_exec")


def agent_run(
    goal: str,
    allowed_tools: Optional[list[str]] = None,
    max_rounds: int = 6,
) -> str:
    """Spawn a bounded sub-agent that investigates *goal* and returns a summary.

    The sub-agent uses the ``pilot`` role model. Settings / model loading are
    done lazily so unit tests can call this function without an LM Studio.
    """
    if not goal or not goal.strip():
        return "ERROR: goal must not be empty."
    allowed = list(allowed_tools or _DEFAULT_READONLY)
    try:
        from openai import OpenAI

        from local_ai_agent_orchestrator.model_manager import ModelManager
        from local_ai_agent_orchestrator.phases import (
            _coder_tool_loop,
            _get_client,
            _llm_call,
        )
        from local_ai_agent_orchestrator.settings import get_settings
        from local_ai_agent_orchestrator.tools import build_openai_schemas
    except Exception as e:
        return f"ERROR: sub-agent runtime unavailable: {e}"

    try:
        s = get_settings()
        cfg = s.models.get("pilot") or s.models.get("planner")
        if cfg is None:
            return "ERROR: no suitable model configured for sub-agent."
        mm = ModelManager()
        model_key = mm.ensure_loaded("pilot" if "pilot" in s.models else "planner")
        client = _get_client()
    except Exception as e:
        return f"ERROR: could not load sub-agent model: {e}"

    system = (
        "You are a bounded LAO sub-agent. Investigate the user's goal using only "
        "the allowed tools, then produce a SHORT (<= 200 words) factual summary "
        "with concrete file paths and line numbers when relevant. "
        f"Max rounds: {max_rounds}. Allowed tools: {', '.join(allowed)}."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": goal.strip()},
    ]
    schemas = build_openai_schemas(allowed)
    try:
        from local_ai_agent_orchestrator.phases import _dispatch_tool_call

        for _round in range(max_rounds):
            resp = _llm_call(
                client, model_key, messages, tools=schemas, max_tokens=cfg.max_completion,
                role="pilot",
            )
            choice = resp.choices[0]
            msg = choice.message
            if not msg.tool_calls:
                return (msg.content or "(no result)").strip()
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                if fn_name not in allowed:
                    result = f"ERROR: tool {fn_name!r} not in sub-agent whitelist"
                else:
                    try:
                        fn_args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        fn_args = {}
                    result = _dispatch_tool_call(
                        fn_name, fn_args, queue=None, task_id=None, phase="subagent"
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result)[:1500],
                    }
                )
        return "(sub-agent reached max_rounds without producing a final answer)"
    except Exception as e:
        return f"ERROR: sub-agent crashed: {e}"


AGENT_RUN = register(
    Tool(
        name="agent_run",
        description=(
            "Spawn a bounded read-only sub-agent for a focused investigation. "
            "Returns a short factual summary."
        ),
        parameters=parameters_schema(
            {
                "goal": param("string", "Single-sentence investigation goal."),
                "allowed_tools": param(
                    "array",
                    "Tool name whitelist; defaults to read-only file & search tools.",
                    items={"type": "string"},
                ),
                "max_rounds": param(
                    "integer",
                    "Hard cap on tool-call rounds (default 6).",
                    default=6,
                ),
            },
            required=["goal"],
        ),
        call=agent_run,
        is_read_only=False,
        is_concurrency_safe=False,
        plan_mode_safe=True,
    )
)
