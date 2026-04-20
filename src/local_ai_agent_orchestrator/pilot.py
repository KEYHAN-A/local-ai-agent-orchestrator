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

from local_ai_agent_orchestrator.interrupts import (
    clear_pilot_round_cancel,
    pilot_round_cancel_pending,
    should_shutdown,
)
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
from local_ai_agent_orchestrator.unified_ui import get_unified_ui
from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.tools import (
    list_dir,
    pick_pilot_tools_workspace,
    push_active_workspace,
    reset_active_workspace,
    tools_workspace_root,
)

log = logging.getLogger(__name__)

# LM Studio may emit huge parallel tool batches; cap keeps the UI responsive.
PILOT_MAX_TOOL_CALLS_PER_MESSAGE = 28


def _ordered_unique_tool_calls(tool_calls: object) -> list:
    """Preserve order, drop exact duplicate (name, arguments) pairs."""
    seen: set[tuple[str, str]] = set()
    out: list = []
    for tc in tool_calls or []:
        fn_name = tc.function.name
        try:
            fn_args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            fn_args = {}
        sig = (fn_name, json.dumps(fn_args, sort_keys=True, default=str))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(tc)
    return out


def _cap_pilot_tool_calls(tool_calls: object, max_n: int) -> list:
    unique = _ordered_unique_tool_calls(tool_calls)
    if len(unique) > max_n:
        log.warning(
            "[Pilot] Capping tool calls from %d to %d (unique by name+args)",
            len(unique),
            max_n,
        )
        return unique[:max_n]
    return unique


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

        pilot_root = get_settings().config_dir.resolve()
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
        "/ideate", "/lock", "/spec", "/done",
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

        if cmd == "/ideate":
            return self._handle_ideate_command(arg)

        if cmd == "/lock":
            return self._handle_lock_command()

        if cmd == "/spec":
            return self._handle_spec_command()

        if cmd == "/done":
            return self._handle_done_command(arg)

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
                "  /ideate <topic>  — Start or continue ideation (Ideator agent)\n"
                "  /lock            — Lock current IDEATION.md\n"
                "  /spec            — Run Spec Doctor: locked IDEATION.md → SPEC.md\n"
                "  /done [plan_id]  — Show DONE-gate report for a plan\n"
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

    def _ideation_workspace(self) -> Path:
        """Workspace where ideation/spec artefacts live (current pilot tools root)."""
        try:
            return Path(tools_workspace_root()).resolve()
        except Exception:
            return get_settings().config_dir.resolve()

    def _emit(self, msg: str) -> None:
        if self._on_assistant_message:
            self._on_assistant_message(msg)

    def _run_ideator(self, workspace: Path, user_text: str) -> str:
        """Call the Ideator model and persist the turn. Returns assistant text."""
        from local_ai_agent_orchestrator import ideation as _ideation
        from local_ai_agent_orchestrator.prompts import build_ideation_messages

        s = get_settings()
        role = "analyst" if "analyst" in s.models else ("planner" if "planner" in s.models else "pilot")
        cfg = s.models[role]
        model_key = self._mm.ensure_loaded(role)
        client = OpenAI(base_url=s.openai_base_url, api_key=s.openai_api_key)

        history = _ideation.read_history(workspace)
        draft = _ideation.read_draft(workspace)
        messages = build_ideation_messages(user_text, history=history, current_draft=draft)
        response = client.chat.completions.create(
            model=model_key,
            messages=messages,
            max_tokens=min(getattr(cfg, "max_completion", 2048) or 2048, 2048),
            temperature=0.4,
            timeout=s.llm_request_timeout_s,
        )
        text = (response.choices[0].message.content or "").strip()
        _ideation.apply_ideator_turn(workspace, user_text, text)
        return text

    def _handle_ideate_command(self, arg: str) -> PilotResult | None:
        from local_ai_agent_orchestrator import ideation as _ideation

        workspace = self._ideation_workspace()
        topic = arg.strip()
        existing = _ideation.read_status(workspace)
        if not existing or not _ideation.read_draft(workspace):
            if not topic:
                self._emit(
                    "Usage: /ideate <topic>\n"
                    "Starts a fresh IDEATION.md draft and asks the first round of questions."
                )
                return None
            _ideation.start_ideation(workspace, topic=topic)
            self._emit(f"Ideator started for: {topic}\nDraft will live at "
                       f"{_ideation.draft_path(workspace).relative_to(workspace) if workspace in _ideation.draft_path(workspace).parents else _ideation.draft_path(workspace)}")
            seed = (
                f"Topic: {topic}\n\n"
                "Begin the IDEATION.md draft and ask your first 1–3 clarifying questions."
            )
        else:
            if _ideation.is_locked(workspace):
                self._emit("IDEATION.md is locked. Use /spec to generate the SPEC, "
                           "or delete .lao/ideation/ to start over.")
                return None
            seed = topic or "Continue refining the draft based on what we have."
        try:
            text = self._run_ideator(workspace, seed)
        except Exception as exc:
            self._emit(f"Ideator failed: {exc}")
            return None
        self._emit(text)
        return None

    def _handle_lock_command(self) -> PilotResult | None:
        from local_ai_agent_orchestrator import ideation as _ideation

        workspace = self._ideation_workspace()
        try:
            dest = _ideation.lock_ideation(workspace)
        except FileNotFoundError as exc:
            self._emit(str(exc))
            return None
        blockers = _ideation.blocking_questions(workspace)
        msg = [f"Locked IDEATION.md → {dest}"]
        if blockers:
            msg.append(f"Note: {len(blockers)} BLOCKING question(s) remain in the draft. "
                       "Spec Doctor will surface them in SPEC.md until you resolve them.")
            msg.extend(f"  • {q}" for q in blockers[:6])
        msg.append("Run /spec next to author SPEC.md from the locked ideation.")
        self._emit("\n".join(msg))
        return None

    def _handle_spec_command(self) -> PilotResult | None:
        from local_ai_agent_orchestrator import ideation as _ideation
        from local_ai_agent_orchestrator.spec_doctor import spec_doctor_phase

        workspace = self._ideation_workspace()
        if not _ideation.is_locked(workspace):
            self._emit("No locked IDEATION.md yet. Run /ideate then /lock first.")
            return None
        try:
            report = spec_doctor_phase(self._mm, workspace)
        except Exception as exc:
            self._emit(f"Spec Doctor failed: {exc}")
            return None
        lines = [
            f"Spec Doctor wrote {report.get('spec_path')}",
            f"Acceptance criteria: {len(report.get('acceptance_ids') or [])} "
            f"({', '.join((report.get('acceptance_ids') or [])[:6])}"
            f"{'...' if len(report.get('acceptance_ids') or []) > 6 else ''})",
        ]
        blocking = report.get("blocking_questions") or []
        if blocking:
            lines.append(f"⚠ {len(blocking)} BLOCKING question(s) still in SPEC.md — resolve before planning:")
            lines.extend(f"  • {q}" for q in blocking[:6])
        lines.append("Next: have the architect produce a plan referencing these AC IDs, "
                     "or use the autopilot pipeline to take it from here.")
        self._emit("\n".join(lines))
        return None

    def _handle_done_command(self, arg: str) -> PilotResult | None:
        from local_ai_agent_orchestrator.done_gate import evaluate_plan_done

        plan_id = arg.strip()
        if not plan_id:
            plans = self._queue.get_plans()
            open_plans = [p for p in plans if p.get("status") != "completed"]
            if not open_plans:
                self._emit("No open plans. Pass a plan id explicitly: /done <plan_id>")
                return None
            plan_id = open_plans[-1]["id"]
        try:
            workspace = self._queue.workspace_for_plan(plan_id)
        except Exception as exc:
            self._emit(f"Could not resolve workspace for plan {plan_id}: {exc}")
            return None
        spec_path = workspace / "SPEC.md"
        try:
            report = evaluate_plan_done(
                self._queue, plan_id, workspace,
                run_acceptance=False,
                spec_doc_path=spec_path if spec_path.exists() else None,
            )
        except Exception as exc:
            self._emit(f"DONE gate evaluation failed: {exc}")
            return None
        verdict = "PASSED" if report.get("plan_done") else "BLOCKED"
        lines = [f"DONE gate for {plan_id}: {verdict}"]
        breakdown = report.get("task_breakdown") or {}
        if breakdown:
            lines.append("  Tasks: " + ", ".join(f"{s}={c}" for s, c in sorted(breakdown.items())))
        ac = report.get("ac_coverage") or {}
        if ac.get("declared"):
            lines.append(f"  Acceptance: {ac.get('passing', 0)}/{ac['declared']} passing")
            if ac.get("missing"):
                lines.append(f"  Missing AC: {', '.join(ac['missing'][:6])}")
        critic = report.get("critic") or {}
        if critic.get("required"):
            lines.append(f"  Critic: {critic.get('approved', 0)}/{critic['required']} approve, "
                         f"{critic.get('rejected', 0)} reject")
        for reason in (report.get("reasons") or [])[:6]:
            lines.append(f"  • {reason}")
        self._emit("\n".join(lines))
        return None

    def _abort_pilot_round_soft(self) -> str:
        """User cancelled the current turn (Ctrl+C); stay in pilot chat."""
        text = (
            "Stopped this reply (Ctrl+C). You can keep chatting, "
            "use /resume for the pipeline, or press Ctrl+C again to exit LAO."
        )
        clear_pilot_round_cancel()
        self._history.append({"role": "assistant", "content": text})
        self._persist_message("assistant", text)
        if self._on_assistant_message:
            self._on_assistant_message(text)
        return text

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
        identical_tool_round_streak = 0
        last_tool_round_fp: tuple[tuple[str, str], ...] | None = None

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

            ui = get_unified_ui()
            if ui is not None:
                ui.set_pilot_cancellable_phase(True)
            try:
                if self._on_llm_round_begin:
                    self._on_llm_round_begin(hint)
                try:
                    kwargs = {
                        "model": model_key,
                        "messages": messages,
                        "max_tokens": cfg.max_completion,
                        "temperature": 0.3,
                        "timeout": s.llm_request_timeout_s,
                        "tools": PILOT_TOOL_SCHEMAS,
                        "tool_choice": "auto",
                        "parallel_tool_calls": False,
                    }
                    try:
                        response = client.chat.completions.create(**kwargs)
                    except TypeError:
                        kwargs.pop("parallel_tool_calls", None)
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
                    elif "model unloaded" in err_str.lower() and should_shutdown():
                        error_msg = (
                            "The local model was unloaded during shutdown. "
                            "Run lao again when you are ready to continue."
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

                if should_shutdown():
                    return "(interrupted)"
                if pilot_round_cancel_pending():
                    return self._abort_pilot_round_soft()

                choice = response.choices[0]
                msg = choice.message

                if msg.tool_calls:
                    capped = _cap_pilot_tool_calls(
                        msg.tool_calls, PILOT_MAX_TOOL_CALLS_PER_MESSAGE
                    )
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
                            for tc in capped
                        ],
                    })

                    sig_cache: dict[tuple[str, str], str] = {}
                    for idx, tc in enumerate(capped):
                        if idx % 5 == 0:
                            if should_shutdown():
                                return "(interrupted)"
                            if pilot_round_cancel_pending():
                                return self._abort_pilot_round_soft()
                        fn_name = tc.function.name
                        try:
                            fn_args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            fn_args = {}

                        sig = (fn_name, json.dumps(fn_args, sort_keys=True, default=str))
                        if sig not in sig_cache:
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

                            sig_cache[sig] = result_str
                        else:
                            result_str = sig_cache[sig]
                            log.info(
                                "[Pilot] Skipping duplicate tool in batch: %s",
                                fn_name,
                            )

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

                    round_fp = tuple(sorted(sig_cache.keys()))
                    had_error_tool = any(
                        (v or "").startswith("ERROR") for v in sig_cache.values()
                    )
                    if round_fp and round_fp == last_tool_round_fp and not had_error_tool:
                        identical_tool_round_streak += 1
                    else:
                        identical_tool_round_streak = 0
                    last_tool_round_fp = round_fp
                    if identical_tool_round_streak >= 2:
                        bail = (
                            "Stopped: the model repeated the same tool calls without "
                            "progress. Try rephrasing, use /status, or /resume if you "
                            "want the pipeline to continue."
                        )
                        log.warning("[Pilot] %s", bail)
                        self._history.append({"role": "assistant", "content": bail})
                        self._persist_message("assistant", bail)
                        if self._on_assistant_message:
                            self._on_assistant_message(bail)
                        return bail

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
            finally:
                if ui is not None:
                    ui.set_pilot_cancellable_phase(False)

        final = "(tool loop ended after max rounds)"
        self._history.append({"role": "assistant", "content": final})
        if self._on_assistant_message:
            self._on_assistant_message(final)
        return final

    def _detect_project_intent(self, text: str) -> str | None:
        """Return a candidate project path/name only for clear switch phrasing."""
        if re.match(r"^/[A-Za-z]", text) and "/" in text[1:]:
            return text.split()[0]

        m = re.search(
            r"\b(?:continue|resume|check|work\s+on|status\s+of|switch\s+to|open)\s+"
            r"(?:[\w\s]{0,48}?\s)?(?:the\s+)?"
            r"([A-Za-z0-9_.-]{2,})\s+(?:project|app)\b",
            text,
            re.IGNORECASE,
        )
        if m:
            tok = m.group(1)
            if self._looks_like_project_token(tok):
                return tok

        m2 = re.search(
            r"\bproject\s+in\s+"
            r"(/[^\s]+|\./[^\s]+|\../[^\s]+|~/[^\s]+|[\w.-]+/[\w./-]+)",
            text,
            re.IGNORECASE,
        )
        if m2:
            return m2.group(1).rstrip(".,;:")

        return None

    @staticmethod
    def _looks_like_project_token(tok: str) -> bool:
        t = (tok or "").strip()
        if len(t) < 2:
            return False
        stop = {
            "the", "a", "an", "this", "that", "my", "our", "your", "their",
            "new", "some", "any", "implementing", "working", "going", "create",
            "source", "code", "for", "with", "from", "into", "and", "to", "on",
            "in", "it", "is", "at", "as", "be", "we", "me", "up", "so", "if",
        }
        if t.lower() in stop:
            return False
        if "/" in t or t.startswith("."):
            return True
        return bool(re.match(r"^[A-Za-z0-9_.-]{2,}$", t))

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
        plan_ws = pick_pilot_tools_workspace(self._queue)
        if plan_ws.resolve() != s.config_dir.resolve():
            parts.append(f"Active plan worktree (generated code): {plan_ws}")

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
