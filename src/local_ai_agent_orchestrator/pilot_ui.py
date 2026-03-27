# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pilot Mode terminal UI.

The Rich-based PilotUI has been replaced by the unified prompt_toolkit UI in
unified_ui.py.  This module keeps PlainPilotUI for --plain / non-TTY fallback
and the enter_pilot_mode() entry point.
"""

from __future__ import annotations

import sys

from local_ai_agent_orchestrator import __version__
from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.pilot import PilotAgent, PilotResult
from local_ai_agent_orchestrator.state import TaskQueue


class PlainPilotUI:
    """
    Minimal non-Rich pilot interface for --plain mode or non-TTY environments.
    """

    _PLAIN_SUFFIXES = ("·", "◆", "◇", "◆")

    def __init__(self, mm: ModelManager, queue: TaskQueue):
        self._mm = mm
        self._queue = queue
        self._plain_think_i = 0

    def run(self) -> PilotResult:
        print(f"\n--- LAO Pilot Mode v{__version__} ---")
        print("Type naturally to chat. /help for commands, /resume to return to autopilot.\n")

        agent = PilotAgent(
            self._mm,
            self._queue,
            on_assistant_message=self._on_message,
            on_tool_call=self._on_tool,
            on_tool_result=self._on_tool_result,
            on_llm_round_begin=self._on_llm_begin,
            on_llm_round_end=self._on_llm_end,
            on_tool_round_begin=self._on_tool_plain,
        )

        try:
            return agent.run(get_input=self._get_input)
        except KeyboardInterrupt:
            return PilotResult.EXIT

    def _on_llm_begin(self, hint: str) -> None:
        sfx = self._PLAIN_SUFFIXES[self._plain_think_i % len(self._PLAIN_SUFFIXES)]
        self._plain_think_i += 1
        print(f"  … {sfx} pilot on '{hint}' (local LLM) …", flush=True)

    def _on_llm_end(self) -> None:
        pass

    def _on_tool_plain(self, name: str) -> None:
        print(f"    > tool: {name}", flush=True)

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

    def _on_tool_result(self, name: str, result: str) -> None:
        short = result.strip().split("\n")[0][:100]
        ok = not short.startswith("ERROR")
        tag = "ok" if ok else "err"
        print(f"    {tag}: {short}")


def enter_pilot_mode(
    mm: ModelManager,
    queue: TaskQueue,
    *,
    use_tui: bool = True,
) -> PilotResult:
    """
    High-level entry point for pilot mode.

    When called from the UnifiedUI path (use_tui=True with TTY), the runner
    handles pilot directly through _run_pilot_with_unified_ui.  This function
    is the fallback for --plain / non-TTY environments.
    """
    ui = PlainPilotUI(mm, queue)
    queue.start_new_pilot_session()
    return ui.run()
