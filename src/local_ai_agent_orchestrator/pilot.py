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
import re
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
from local_ai_agent_orchestrator.tools import (
    list_dir,
    pick_pilot_tools_workspace,
    push_active_workspace,
    reset_active_workspace,
    tools_workspace_root,
)

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
        self._pilot_ws_token: object | None = None

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

        pilot_root = pick_pilot_tools_workspace(self._queue)
        self._pilot_ws_token = push_active_workspace(pilot_root)
        log.info("[Pilot] Tools workspace: %s", pilot_root)

        try:
            return self._run_loop(get_input, client, model_key, cfg)
        finally:
            if self._pilot_ws_token is not None:
                reset_active_workspace(self._pilot_ws_token)
                self._pilot_ws_token = None

    def _run_loop(self, get_input: object, client, model_key: str, cfg) -> PilotResult:
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

            project_candidate = self._detect_project_intent(user_text)
            if project_candidate:
                switch_msg = self._resolve_and_switch_project(project_candidate)
                if self._on_assistant_message:
                    self._on_assistant_message(switch_msg)
                if "Switched to project" in switch_msg:
                    context = self._build_context()
                    messages = build_pilot_messages(context, self._history)

            self._history.append({"role": "user", "content": user_text})
            self._persist_message("user", user_text)

            response_text = self._tool_loop(client, model_key, cfg)

            if is_resume_requested():
                log.info("[Pilot] Resuming pipeline (tool-triggered)")
                return PilotResult.RESUME_PIPELINE

        return PilotResult.EXIT

    _KNOWN_SLASH_COMMANDS = frozenset({
        "/exit", "/quit", "/resume", "/continue", "/go",
        "/clear", "/status", "/help", "/project", "/gates",
    })

    def _handle_slash_command(self, text: str) -> PilotResult | None:
        """Handle /commands. Returns PilotResult if the command exits the loop, else None.

        Only recognized commands are handled; input that starts with ``/`` but
        is not a known command (e.g. an absolute path like ``/Users/...``) is
        passed through to the LLM as a regular message.
        """
        if not text.startswith("/"):
            return None

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd not in self._KNOWN_SLASH_COMMANDS:
            return None  # Not a command — let the LLM handle it

        if cmd in ("/exit", "/quit"):
            return PilotResult.EXIT

        if cmd in ("/resume", "/continue", "/go"):
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

        if cmd == "/gates":
            from local_ai_agent_orchestrator.pilot_tools import gate_summary
            summary = gate_summary(arg.strip() or None)
            if self._on_assistant_message:
                self._on_assistant_message(summary)
            return None

        if cmd == "/project":
            return self._handle_project_command(arg)

        if cmd == "/help":
            help_text = (
                "Available commands:\n"
                "  /status          — Show pipeline status\n"
                "  /gates           — Show validation profile and inferred build/lint hints\n"
                "  /resume          — Return to autopilot pipeline\n"
                "  /clear           — Clear chat history\n"
                "  /project         — List registered projects\n"
                "  /project use X   — Switch to project X\n"
                "  /project scan    — Scan for LAO projects\n"
                "  /help            — Show this help\n"
                "  /exit            — Exit LAO\n"
                "\nOr just type naturally to chat with the pilot agent."
            )
            if self._on_assistant_message:
                self._on_assistant_message(help_text)
            return None

        return None

    def _handle_project_command(self, arg: str) -> PilotResult | None:
        """Handle /project sub-commands."""
        from local_ai_agent_orchestrator.project_registry import ProjectRegistry

        reg = ProjectRegistry()
        sub = arg.strip().split(maxsplit=1)
        sub_cmd = sub[0].lower() if sub else "list"
        sub_arg = sub[1].strip() if len(sub) > 1 else ""

        if sub_cmd in ("list", ""):
            entries = reg.list_all()
            if not entries:
                msg = "No projects registered. Run /project scan to discover LAO projects."
            else:
                lines = ["Registered projects:"]
                for e in entries:
                    status_parts = []
                    if e.pending_tasks:
                        status_parts.append(f"{e.pending_tasks} pending")
                    if e.failed_tasks:
                        status_parts.append(f"{e.failed_tasks} failed")
                    if e.plans_count:
                        status_parts.append(f"{e.plans_count} plans")
                    tag = f" ({', '.join(status_parts)})" if status_parts else ""
                    lines.append(f"  {e.name}{tag}  {e.path}")
                msg = "\n".join(lines)
            if self._on_assistant_message:
                self._on_assistant_message(msg)
            return None

        if sub_cmd == "scan":
            root = Path(sub_arg).expanduser() if sub_arg else get_settings().config_dir
            found = reg.scan(root)
            if found:
                lines = [f"Found {len(found)} project(s):"]
                for e in found:
                    lines.append(f"  {e.name}  {e.path}")
                msg = "\n".join(lines)
            else:
                msg = f"No LAO projects found under {root}"
            if self._on_assistant_message:
                self._on_assistant_message(msg)
            return None

        if sub_cmd == "use":
            if not sub_arg:
                if self._on_assistant_message:
                    self._on_assistant_message("Usage: /project use <name-or-path>")
                return None
            ctx = self._resolve_and_switch_project(sub_arg)
            if self._on_assistant_message:
                self._on_assistant_message(ctx)
            return None

        if sub_cmd == "status":
            target = sub_arg or None
            if target:
                entry = reg.get(target)
                if entry:
                    entry = reg.refresh(entry)
                    lines = [
                        f"Project: {entry.name}",
                        f"  Path: {entry.path}",
                        f"  Config: {'yes' if entry.has_config else 'no'}",
                        f"  Plans: {entry.plans_count}",
                        f"  Pending: {entry.pending_tasks}  Failed: {entry.failed_tasks}",
                    ]
                    msg = "\n".join(lines)
                else:
                    msg = f"Project '{target}' not found. Run /project list."
            else:
                msg = f"Current workspace: {get_settings().config_dir}"
            if self._on_assistant_message:
                self._on_assistant_message(msg)
            return None

        if self._on_assistant_message:
            self._on_assistant_message(
                f"Unknown /project sub-command: {sub_cmd}. "
                "Try: /project list, /project use <name>, /project scan, /project status"
            )
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

        consecutive_errors = 0

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

                    if result_str.startswith("ERROR"):
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 0

                    if consecutive_errors >= 4:
                        bail_msg = (
                            "I've hit several errors in a row trying to access "
                            "that resource. Could you provide the exact path or "
                            "project name? You can also use /project scan to "
                            "discover LAO projects, or /project use <name> to "
                            "switch workspace."
                        )
                        self._history.append({"role": "assistant", "content": bail_msg})
                        self._persist_message("assistant", bail_msg)
                        if self._on_assistant_message:
                            self._on_assistant_message(bail_msg)
                        return bail_msg

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
                content = msg.content or self._build_fallback_response()
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

    def _detect_project_intent(self, text: str) -> str | None:
        """Return a candidate project path/name if the message references one."""
        if re.match(r"^/[A-Za-z]", text) and "/" in text[1:]:
            return text.split()[0]
        patterns = [
            r"(?:continue|resume|check|work on|status of|switch to|open)\s+(?:[\w\s]*?\s)?(?:the\s+)?(\S+)\s+(?:project|plan|app)",
            r"(?:project|plan)\s+(?:in\s+)?([/\w.-]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    def _resolve_and_switch_project(self, candidate: str) -> str:
        """Try to resolve a candidate to a known project and switch workspace."""
        from local_ai_agent_orchestrator.project_registry import ProjectRegistry

        reg = ProjectRegistry()

        p = Path(candidate).expanduser()
        if p.is_dir():
            if (p / "factory.yaml").exists() or (p / "factory.yml").exists():
                entry = reg.add(p)
                return self._switch_to_project(entry)
            plans_dir = p / "plans"
            if plans_dir.is_dir() and any(plans_dir.glob("*.md")):
                entry = reg.add(p)
                return self._switch_to_project(entry)

        entry = reg.get(candidate)
        if entry:
            return self._switch_to_project(entry)

        all_projects = reg.list_all()
        matches = [e for e in all_projects if candidate.lower() in e.name.lower()]
        if len(matches) == 1:
            return self._switch_to_project(matches[0])
        if matches:
            listing = ", ".join(f"{e.name} ({e.path})" for e in matches)
            return f"Multiple projects match '{candidate}': {listing}. Which one?"

        return f"Project '{candidate}' not found. Try /project scan to discover projects."

    def _switch_to_project(self, entry) -> str:
        """Re-initialize settings for the given project entry."""
        from local_ai_agent_orchestrator.settings import init_settings

        project_path = Path(entry.path)
        config_file = project_path / "factory.yaml"
        if not config_file.exists():
            config_file = project_path / "factory.yml"

        try:
            if config_file.exists():
                init_settings(config_path=config_file, cwd=project_path)
            else:
                init_settings(cwd=project_path)

            new_root = project_path.resolve()
            if self._pilot_ws_token is not None:
                reset_active_workspace(self._pilot_ws_token)
            self._pilot_ws_token = push_active_workspace(new_root)

            s = get_settings()
            self._queue = TaskQueue(s.state_db)
            bind_queue(self._queue)

            context = self._build_context()
            return f"Switched to project '{entry.name}' at {entry.path}\n\n{context}"
        except Exception as exc:
            return f"Failed to switch to project '{entry.name}': {exc}"

    def _build_fallback_response(self) -> str:
        """Structured fallback when the LLM returns an empty response."""
        parts = ["I wasn't able to complete that request."]
        s = get_settings()
        config_yaml = s.config_dir / "factory.yaml"
        config_yml = s.config_dir / "factory.yml"
        if not config_yaml.exists() and not config_yml.exists():
            parts.append(
                f"No factory.yaml found in the current directory ({s.config_dir})."
            )
        stats = self._queue.get_stats()
        if not stats or all(v == 0 for v in stats.values()):
            parts.append("The task queue is empty.")
        parts.append(
            "Try: provide an exact path, use /project to switch workspace, "
            "or rephrase your request."
        )
        return " ".join(parts)

    def _build_context(self) -> str:
        """Gather workspace and pipeline state for the system prompt."""
        parts = []

        s = get_settings()
        parts.append(f"Workspace: {s.config_dir}")
        parts.append(f"Plans directory: {s.plans_dir}")
        parts.append(f"Pilot tool root (list_dir, file_read, shell_exec): {tools_workspace_root()}")

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
