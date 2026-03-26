# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pilot Mode terminal UI.

Rich-based interactive chat interface with:
- Color-coded chat history (user, assistant, tool calls)
- Bottom-anchored input prompt
- Status header with pipeline state
- Graceful transition to/from the RunDashboard
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from local_ai_agent_orchestrator import __version__
from local_ai_agent_orchestrator.branding import DISPLAY as D
from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.pilot import PilotAgent, PilotResult
from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.settings import get_settings

log = logging.getLogger(__name__)


class _ChatMessage:
    __slots__ = ("role", "content")

    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


class PilotUI:
    """
    Full-screen Rich chat interface for Pilot Mode.

    Handles rendering the chat history and collecting user input.
    Delegates agent logic to PilotAgent.
    """

    def __init__(self, mm: ModelManager, queue: TaskQueue):
        self._mm = mm
        self._queue = queue
        self._console = Console(force_terminal=True)
        self._messages: deque[_ChatMessage] = deque(maxlen=200)
        self._lock = threading.Lock()
        self._input_ready = threading.Event()
        self._current_input: Optional[str] = None
        self._running = False

    def run(self) -> PilotResult:
        """Main entry point: run the pilot chat UI and return a PilotResult."""
        self._running = True
        self._messages.clear()

        self._print_welcome()

        agent = PilotAgent(
            self._mm,
            self._queue,
            on_assistant_message=self._on_assistant_message,
            on_tool_call=self._on_tool_call,
        )

        try:
            result = agent.run(get_input=self._get_user_input)
        except KeyboardInterrupt:
            result = PilotResult.EXIT
        finally:
            self._running = False

        return result

    def _print_welcome(self) -> None:
        stats = self._queue.get_stats()
        plans = self._queue.get_plans()

        header = Text()
        header.append(" LAO ", style=f"bold {D['AI_SPARK_BRIGHT']}")
        header.append(f" Pilot Mode v{__version__} ", style=f"bold {D['TEXT']}")

        status_parts = []
        if stats:
            total = sum(stats.values())
            completed = stats.get("completed", 0)
            failed = stats.get("failed", 0)
            status_parts.append(f"Tasks: {completed}/{total} done")
            if failed:
                status_parts.append(f"{failed} failed")
        if plans:
            active = [p for p in plans if p["status"] != "completed"]
            done = [p for p in plans if p["status"] == "completed"]
            if active:
                status_parts.append(f"Active plans: {len(active)}")
            if done:
                status_parts.append(f"Completed plans: {len(done)}")

        subtitle = " | ".join(status_parts) if status_parts else "No pipeline activity"

        self._console.print()
        self._console.print(
            Panel.fit(
                Group(header, Text(subtitle, style=D["TEXT_MUTED"])),
                border_style=D["AI_SPARK"],
                style=f"on {D['BG']}",
                padding=(1, 2),
            )
        )
        self._console.print()

        help_hint = Text()
        help_hint.append("  Type naturally to chat. ", style=D["TEXT_MUTED"])
        help_hint.append("/help", style=D["AI_SPARK_BRIGHT"])
        help_hint.append(" for commands, ", style=D["TEXT_MUTED"])
        help_hint.append("/resume", style=D["AI_SPARK_BRIGHT"])
        help_hint.append(" to return to autopilot, ", style=D["TEXT_MUTED"])
        help_hint.append("Ctrl+C", style=D["WARNING_BRIGHT"])
        help_hint.append(" to exit.", style=D["TEXT_MUTED"])
        self._console.print(help_hint)
        self._console.print()

    def _get_user_input(self) -> str | None:
        """Prompt the user for input. Returns None on EOF/Ctrl+C."""
        try:
            prompt = Text()
            prompt.append("pilot", style=f"bold {D['AI_SPARK_BRIGHT']}")
            prompt.append("> ", style=D["TEXT_MUTED"])

            self._console.print(prompt, end="")
            line = input()
            if line is None:
                return None

            user_text = line.strip()

            if user_text and not user_text.startswith("/"):
                self._append_message("user", user_text)

            return user_text

        except (EOFError, KeyboardInterrupt):
            self._console.print()
            return None

    def _on_assistant_message(self, content: str) -> None:
        """Callback from PilotAgent when the assistant produces a response."""
        self._append_message("assistant", content)
        self._render_assistant_message(content)

    def _on_tool_call(self, name: str, args: dict) -> None:
        """Callback from PilotAgent when a tool is called."""
        args_summary = ", ".join(f"{k}={repr(v)[:60]}" for k, v in args.items())
        tool_line = f"{name}({args_summary})"
        self._append_message("tool", tool_line)
        self._render_tool_call(tool_line)

    def _append_message(self, role: str, content: str) -> None:
        with self._lock:
            self._messages.append(_ChatMessage(role, content))

    def _render_assistant_message(self, content: str) -> None:
        self._console.print()
        prefix = Text()
        prefix.append("  [Pilot] ", style=f"bold {D['AI_SPARK']}")
        self._console.print(prefix, end="")

        for line in content.split("\n"):
            self._console.print(Text(f"  {line}", style=D["TEXT"]))

        self._console.print()

    def _render_tool_call(self, tool_line: str) -> None:
        text = Text()
        text.append("    tool: ", style=D["TEXT_MUTED"])
        text.append(tool_line, style=D["PANEL_ELEVATED"])
        self._console.print(text)


class PlainPilotUI:
    """
    Minimal non-Rich pilot interface for --plain mode or non-TTY environments.
    """

    def __init__(self, mm: ModelManager, queue: TaskQueue):
        self._mm = mm
        self._queue = queue

    def run(self) -> PilotResult:
        print(f"\n--- LAO Pilot Mode v{__version__} ---")
        print("Type naturally to chat. /help for commands, /resume to return to autopilot.\n")

        agent = PilotAgent(
            self._mm,
            self._queue,
            on_assistant_message=self._on_message,
            on_tool_call=self._on_tool,
        )

        try:
            return agent.run(get_input=self._get_input)
        except KeyboardInterrupt:
            return PilotResult.EXIT

    def _get_input(self) -> str | None:
        try:
            line = input("pilot> ")
            return line
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    def _on_message(self, content: str) -> None:
        print(f"\n[Pilot] {content}\n")

    def _on_tool(self, name: str, args: dict) -> None:
        print(f"  tool: {name}({args})")


def enter_pilot_mode(
    mm: ModelManager,
    queue: TaskQueue,
    *,
    use_tui: bool = True,
) -> PilotResult:
    """
    High-level entry point for pilot mode.
    Chooses between Rich UI and plain mode based on terminal capabilities.
    """
    if use_tui and sys.stdout.isatty():
        ui = PilotUI(mm, queue)
    else:
        ui = PlainPilotUI(mm, queue)

    queue.start_new_pilot_session()
    return ui.run()
