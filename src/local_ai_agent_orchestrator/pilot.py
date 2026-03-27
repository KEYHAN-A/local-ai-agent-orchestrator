# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pilot Mode agent engine.

Provides an interactive chat loop that activates when the LAO pipeline is idle.
The pilot can execute workspace tools, create plans for the pipeline, and
transition back to autopilot mode.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

from openai import OpenAI

from local_ai_agent_orchestrator.interrupts import should_shutdown
from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.pilot_tools import (
    PILOT_TOOL_DISPATCH,
    PILOT_TOOL_SCHEMAS,
    bind_queue,
    is_resume_requested,
    reset_resume_flag,
)
from local_ai_agent_orchestrator.prompts import build_pilot_messages
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.tools import list_dir

log = logging.getLogger(__name__)


class PilotResult(Enum):
    CONTINUE = auto()
    RESUME_PIPELINE = auto()
    EXIT = auto()


class PilotAgent:
    """Interactive chat agent that operates when the pipeline is idle."""

    def __init__(
        self,
        mm: ModelManager,
        queue: TaskQueue,
        *,
        on_assistant_message: Optional[object] = None,
        on_tool_call: Optional[object] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_user_prompt: Optional[object] = None,
        on_llm_round_begin: Optional[Callable[[str], None]] = None,
        on_llm_round_end: Optional[Callable[[], None]] = None,
        on_tool_round_begin: Optional[Callable[[str], None]] = None,
        on_usage: Optional[Callable[[int, int], None]] = None,
    ):
        self._mm = mm
        self._queue = queue
        self._history: list[dict] = []
        self._session_start = time.time()
        self._on_assistant_message = on_assistant_message
        self._on_tool_call = on_tool_call
        self._on_tool_result = on_tool_result
        self._on_user_prompt = on_user_prompt
        self._on_llm_round_begin = on_llm_round_begin
        self._on_llm_round_end = on_llm_round_end
        self._on_tool_round_begin = on_tool_round_begin
        self._on_usage = on_usage

        bind_queue(queue)

    def run(self, get_input: object) -> PilotResult:
        """
        Main pilot loop.

        get_input: callable that returns user text or None to exit.
                   Signature: () -> str | None
        """
        s = get_settings()
        cfg = s.models["pilot"]
        model_key = self._mm.ensure_loaded("pilot")
        client = OpenAI(base_url=s.openai_base_url, api_key=s.openai_api_key)

        reset_resume_flag()

        log.info("[Pilot] Entering Pilot Mode")

        while not should_shutdown():
            if is_resume_requested():
                log.info("[Pilot] Resuming pipeline (tool-triggered)")
                return PilotResult.RESUME_PIPELINE

            user_text = get_input()
            if user_text is None:
                return PilotResult.EXIT

            user_text = user_text.strip()
            if not user_text:
                continue

            slash_result = self._handle_slash_command(user_text)
            if slash_result is not None:
                return slash_result

            self._history.append({"role": "user", "content": user_text})
            self._persist_message("user", user_text)

            response_text = self._tool_loop(client, model_key, cfg)

            if is_resume_requested():
                log.info("[Pilot] Resuming pipeline (tool-triggered)")
                return PilotResult.RESUME_PIPELINE

        return PilotResult.EXIT

    def _handle_slash_command(self, text: str) -> PilotResult | None:
        """Handle /commands. Returns PilotResult if the command exits the loop, else None."""
        if not text.startswith("/"):
            return None

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/exit" or cmd == "/quit":
            return PilotResult.EXIT

        if cmd == "/resume" or cmd == "/continue" or cmd == "/go":
            return PilotResult.RESUME_PIPELINE

        if cmd == "/clear":
            self._history.clear()
            if self._on_assistant_message:
                self._on_assistant_message("Chat history cleared.")
            return None

        if cmd == "/status":
            from local_ai_agent_orchestrator.pilot_tools import pipeline_status
            status = pipeline_status()
            if self._on_assistant_message:
                self._on_assistant_message(status)
            return None

        if cmd == "/help":
            help_text = (
                "Available commands:\n"
                "  /status   — Show pipeline status\n"
                "  /resume   — Return to autopilot pipeline\n"
                "  /clear    — Clear chat history\n"
                "  /help     — Show this help\n"
                "  /exit     — Exit LAO\n"
                "\nOr just type naturally to chat with the pilot agent."
            )
            if self._on_assistant_message:
                self._on_assistant_message(help_text)
            return None

        if self._on_assistant_message:
            self._on_assistant_message(f"Unknown command: {cmd}. Type /help for options.")
        return None

    def _tool_loop(
        self,
        client: OpenAI,
        model_key: str,
        cfg: object,
        max_rounds: int = 15,
    ) -> str:
        """
        Send the conversation to the LLM, handle tool calls, and return the final response.
        """
        s = get_settings()
        context = self._build_context()
        messages = build_pilot_messages(context, self._history)

        for round_num in range(max_rounds):
            if should_shutdown():
                return "(interrupted)"

            hints = (
                "weaving context from your workspace",
                "consulting the local model",
                "composing the next move",
                "tracing plan and tooling paths",
            )
            hint = hints[round_num % len(hints)]

            if self._on_llm_round_begin:
                self._on_llm_round_begin(hint)
            try:
                try:
                    kwargs = {
                        "model": model_key,
                        "messages": messages,
                        "max_tokens": cfg.max_completion,
                        "temperature": 0.3,
                        "timeout": s.llm_request_timeout_s,
                        "tools": PILOT_TOOL_SCHEMAS,
                        "tool_choice": "auto",
                    }
                    response = client.chat.completions.create(**kwargs)
                except Exception as e:
                    err_str = str(e)
                    if "timeout" in err_str.lower() or "timed out" in err_str.lower():
                        error_msg = (
                            f"LLM request timed out ({err_str}). "
                            "The model may be loading or the request was too large. "
                            "Try again or simplify your request."
                        )
                    elif "connection" in err_str.lower() or "refused" in err_str.lower():
                        error_msg = (
                            f"Cannot reach LM Studio ({err_str}). "
                            "Check that the server is running and try again."
                        )
                    else:
                        error_msg = f"LLM error: {err_str}"
                    log.error(f"[Pilot] {error_msg}")
                    self._history.append({"role": "assistant", "content": error_msg})
                    if self._on_assistant_message:
                        self._on_assistant_message(error_msg)
                    return error_msg
            finally:
                if self._on_llm_round_end:
                    self._on_llm_round_end()

            choice = response.choices[0]
            msg = choice.message

            if msg.tool_calls:
                messages.append({
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
                })

                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}

                    if self._on_tool_round_begin:
                        self._on_tool_round_begin(fn_name)

                    if self._on_tool_call:
                        self._on_tool_call(fn_name, fn_args)

                    log.info(f"[Pilot] Tool: {fn_name}({list(fn_args.keys())})")

                    if fn_name in PILOT_TOOL_DISPATCH:
                        result = PILOT_TOOL_DISPATCH[fn_name](**fn_args)
                    else:
                        result = f"ERROR: Unknown tool '{fn_name}'"

                    result_str = str(result)[:4000]
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })

                    if self._on_tool_result:
                        self._on_tool_result(fn_name, result_str)

                    if is_resume_requested():
                        final = msg.content or (
                            "Returning to autopilot. The pipeline will pick up "
                            "pending tasks and new plans automatically."
                        )
                        self._history.append({"role": "assistant", "content": final})
                        self._persist_message("assistant", final)
                        if self._on_assistant_message:
                            self._on_assistant_message(final)
                        return final

                if len(messages) > 40:
                    messages = [messages[0]] + messages[-30:]

            else:
                content = msg.content or (
                    "The model returned an empty response. This can happen with "
                    "complex queries -- try rephrasing or breaking the request "
                    "into smaller steps."
                )
                self._history.append({"role": "assistant", "content": content})
                self._persist_message("assistant", content)
                if self._on_assistant_message:
                    self._on_assistant_message(content)

                usage = response.usage
                if usage:
                    self._queue.log_run(
                        task_id=None,
                        phase="pilot",
                        model_key=model_key,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        duration_seconds=0.0,
                        success=True,
                    )
                    if self._on_usage:
                        self._on_usage(usage.prompt_tokens, usage.completion_tokens)
                return content

        final = "(tool loop ended after max rounds)"
        self._history.append({"role": "assistant", "content": final})
        if self._on_assistant_message:
            self._on_assistant_message(final)
        return final

    def _build_context(self) -> str:
        """Gather workspace and pipeline state for the system prompt."""
        parts = []

        s = get_settings()
        parts.append(f"Workspace: {s.config_dir}")
        parts.append(f"Plans directory: {s.plans_dir}")

        stats = self._queue.get_stats()
        if stats:
            stat_line = ", ".join(f"{k}: {v}" for k, v in sorted(stats.items()))
            parts.append(f"Task queue: {stat_line}")
        else:
            parts.append("Task queue: empty")

        plans = self._queue.get_plans()
        if plans:
            for p in plans[-3:]:
                plan_tasks = self._queue.get_plan_tasks(p["id"])
                failed = [t for t in plan_tasks if t.status == "failed"]
                completed = [t for t in plan_tasks if t.status == "completed"]
                pending = [t for t in plan_tasks if t.status == "pending"]
                parts.append(
                    f"Plan '{p['filename']}' [{p['status']}]: "
                    f"{len(completed)}/{len(plan_tasks)} done, "
                    f"{len(pending)} pending"
                    + (f", {len(failed)} failed" if failed else "")
                )
                if failed:
                    for t in failed[:5]:
                        feedback = (t.reviewer_feedback or "")[:300]
                        reason = t.escalation_reason or "unknown"
                        parts.append(
                            f"  FAILED #{t.id} '{t.title}' "
                            f"(reason: {reason}, attempts: {t.attempt}): {feedback}"
                        )

        tokens = self._queue.get_total_tokens()
        total_tok = tokens["prompt_tokens"] + tokens["completion_tokens"]
        if total_tok > 0:
            parts.append(f"Total tokens used this session: {total_tok:,}")

        try:
            tree = list_dir(".", max_depth=2)
            if tree and not tree.startswith("ERROR"):
                lines = tree.splitlines()[:30]
                parts.append(f"Project structure:\n" + "\n".join(lines))
        except Exception:
            pass

        cwd = str(s.config_dir)
        try:
            git_status = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=5, cwd=cwd,
            )
            if git_status.returncode == 0 and git_status.stdout.strip():
                status_lines = git_status.stdout.strip().splitlines()[:20]
                parts.append(f"Git status:\n" + "\n".join(status_lines))
        except Exception:
            pass

        try:
            git_log = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                capture_output=True, text=True, timeout=5, cwd=cwd,
            )
            if git_log.returncode == 0 and git_log.stdout.strip():
                parts.append(f"Recent git commits:\n{git_log.stdout.strip()}")
        except Exception:
            pass

        try:
            git_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, timeout=5, cwd=cwd,
            )
            if git_branch.returncode == 0 and git_branch.stdout.strip():
                parts.append(f"Current branch: {git_branch.stdout.strip()}")
        except Exception:
            pass

        return "\n".join(parts)

    def _persist_message(self, role: str, content: str) -> None:
        """Persist conversation message to SQLite if available."""
        try:
            self._queue.log_pilot_message(role, content)
        except Exception:
            pass

    @property
    def history(self) -> list[dict]:
        return list(self._history)
