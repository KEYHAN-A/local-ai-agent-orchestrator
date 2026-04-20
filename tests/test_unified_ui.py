# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the unified terminal UI (unified_ui.py) — Professional Track."""

from __future__ import annotations

import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.unified_ui import (
    EventKind,
    RenderBus,
    RenderEvent,
    SlashCommandCompleter,
    TerminalCapabilities,
    UIMode,
    UnifiedUI,
    ViewComposer,
    _detect_color_support,
    _model_swap_mini_bar,
    _model_swap_mini_bar_html,
    _strip_ansi,
    apply_runner_context,
    get_unified_ui,
    sanitize_for_terminal,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _plain_caps() -> TerminalCapabilities:
    """Return a plain (no-color, non-interactive) capability snapshot for tests."""
    from dataclasses import replace
    caps = TerminalCapabilities.probe()
    return replace(caps, mode=UIMode.PLAIN, supports_color=False, interactive=False)


def _rich_caps() -> TerminalCapabilities:
    from dataclasses import replace
    caps = TerminalCapabilities.probe()
    return replace(caps, mode=UIMode.RICH, supports_color=True, interactive=True, color_depth=256)


# ─────────────────────────────────────────────────────────────────────────────
# TerminalCapabilities
# ─────────────────────────────────────────────────────────────────────────────

class TestTerminalCapabilities(unittest.TestCase):
    def test_probe_returns_instance(self):
        caps = TerminalCapabilities.probe()
        self.assertIsInstance(caps, TerminalCapabilities)

    def test_lao_ui_mode_plain_forces_plain(self):
        with patch.dict("os.environ", {"LAO_UI_MODE": "plain"}, clear=False):
            caps = TerminalCapabilities.probe()
        self.assertEqual(caps.mode, UIMode.PLAIN)
        self.assertFalse(caps.rich)

    def test_lao_ui_mode_rich_forces_rich(self):
        with patch.dict("os.environ", {"LAO_UI_MODE": "rich", "LAO_COLOR": "1"}, clear=False):
            caps = TerminalCapabilities.probe()
        self.assertEqual(caps.mode, UIMode.RICH)
        self.assertTrue(caps.rich)

    def test_no_color_env_disables_color(self):
        with patch.dict("os.environ", {"NO_COLOR": "1"}, clear=False):
            caps = TerminalCapabilities.probe()
        self.assertFalse(caps.supports_color)
        self.assertEqual(caps.color_depth, 0)

    def test_lao_color_1_enables_color(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "NO_COLOR"}
        env["LAO_COLOR"] = "1"
        with patch.dict("os.environ", env, clear=True):
            caps = TerminalCapabilities.probe()
        self.assertTrue(caps.supports_color)
        self.assertEqual(caps.color_depth, 16777216)

    def test_lao_color_0_disables_color(self):
        with patch.dict("os.environ", {"LAO_COLOR": "0"}, clear=False):
            caps = TerminalCapabilities.probe()
        self.assertFalse(caps.supports_color)

    def test_colorterm_truecolor_sets_depth(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "NO_COLOR"}
        env.update({"COLORTERM": "truecolor", "LAO_COLOR": "1"})
        with patch.dict("os.environ", env, clear=True):
            caps = TerminalCapabilities.probe()
        self.assertEqual(caps.color_depth, 16777216)

    def test_width_clamped(self):
        caps = TerminalCapabilities.probe()
        self.assertGreaterEqual(caps.width, 60)
        self.assertLessEqual(caps.width, 220)


# ─────────────────────────────────────────────────────────────────────────────
# Sanitisation helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitisation(unittest.TestCase):
    def test_strip_ansi_removes_sgr(self):
        self.assertEqual(_strip_ansi("\x1b[35mhello\x1b[0m world"), "hello world")

    def test_strip_ansi_removes_truecolor(self):
        self.assertEqual(_strip_ansi("\x1b[38;2;99;102;241mtext\x1b[0m"), "text")

    def test_strip_ansi_empty_string(self):
        self.assertEqual(_strip_ansi(""), "")

    def test_strip_ansi_no_escapes(self):
        self.assertEqual(_strip_ansi("plain text"), "plain text")

    def test_sanitize_strips_ansi(self):
        self.assertEqual(sanitize_for_terminal("\x1b[1mhello\x1b[0m"), "hello")

    def test_sanitize_wraps_long_lines(self):
        long = "x" * 200
        result = sanitize_for_terminal(long, width=80)
        for line in result.splitlines():
            self.assertLessEqual(len(line), 80)

    def test_detect_color_support_respects_no_color(self):
        with patch.dict("os.environ", {"NO_COLOR": "1"}, clear=False):
            self.assertFalse(_detect_color_support())

    def test_detect_color_support_lao_color_1(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "NO_COLOR"}
        env["LAO_COLOR"] = "1"
        with patch.dict("os.environ", env, clear=True):
            self.assertTrue(_detect_color_support())


# ─────────────────────────────────────────────────────────────────────────────
# RenderBus
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderBus(unittest.TestCase):
    def test_consumer_receives_events(self):
        bus = RenderBus()
        received: list[RenderEvent] = []
        bus.set_consumer(received.append)
        bus.put(RenderEvent(EventKind.INFO, {"msg": "hello"}))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].kind, EventKind.INFO)

    def test_events_queued_before_consumer(self):
        bus = RenderBus()
        bus.put(RenderEvent(EventKind.INFO, {"msg": "pre-consumer"}))
        received: list[RenderEvent] = []
        bus.drain_pending(received.append)
        self.assertEqual(len(received), 1)

    def test_thread_safety(self):
        bus = RenderBus()
        received: list[RenderEvent] = []
        lock = threading.Lock()

        def safe_append(ev: RenderEvent) -> None:
            with lock:
                received.append(ev)

        bus.set_consumer(safe_append)
        threads = [
            threading.Thread(target=lambda: bus.put(RenderEvent(EventKind.INFO, {"msg": f"t{i}"})))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(received), 20)

    def test_consumer_can_be_cleared(self):
        bus = RenderBus()
        received: list[RenderEvent] = []
        bus.set_consumer(received.append)
        bus.set_consumer(None)
        bus.put(RenderEvent(EventKind.INFO, {"msg": "after clear"}))
        self.assertEqual(len(received), 0)


# ─────────────────────────────────────────────────────────────────────────────
# ViewComposer
# ─────────────────────────────────────────────────────────────────────────────

class TestViewComposerPlain(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)
        self.composer = ViewComposer(_plain_caps())

    def tearDown(self):
        reset_settings_for_tests()
        self._td.cleanup()

    def _compose_str(self, event: RenderEvent) -> str:
        return "\n".join(str(r) for r in self.composer.compose(event))

    def test_info_event(self):
        result = self._compose_str(RenderEvent(EventKind.INFO, {"msg": "test message"}))
        self.assertIn("test message", result)

    def test_error_event_with_suggestion(self):
        result = self._compose_str(
            RenderEvent(EventKind.ERROR, {"msg": "something broke", "suggestion": "try again"})
        )
        self.assertIn("something broke", result)
        self.assertIn("try again", result)

    def test_user_msg_event(self):
        result = self._compose_str(RenderEvent(EventKind.USER_MSG, {"content": "hello pilot"}))
        self.assertIn("hello pilot", result)
        self.assertIn("You", result)

    def test_assistant_msg_event(self):
        result = self._compose_str(RenderEvent(EventKind.ASSISTANT_MSG, {"content": "I can help"}))
        self.assertIn("I can help", result)
        self.assertIn("Pilot", result)

    def test_tool_call_event(self):
        result = self._compose_str(
            RenderEvent(EventKind.TOOL_CALL, {"name": "list_dir", "args": {"path": "."}})
        )
        self.assertIn("list_dir", result)

    def test_tool_result_ok(self):
        result = self._compose_str(
            RenderEvent(EventKind.TOOL_RESULT, {"name": "list_dir", "result": "src/\ntests/"})
        )
        self.assertIn("list_dir", result)

    def test_tool_result_error(self):
        result = self._compose_str(
            RenderEvent(EventKind.TOOL_RESULT, {"name": "run_cmd", "result": "ERROR: not found"})
        )
        self.assertIn("run_cmd", result)

    def test_usage_event(self):
        result = self._compose_str(RenderEvent(EventKind.USAGE, {"prompt": 100, "completion": 50}))
        self.assertIn("150", result)

    def test_transition_event(self):
        result = self._compose_str(
            RenderEvent(EventKind.TRANSITION, {"from_mode": "Pipeline", "to_mode": "Pilot"})
        )
        self.assertIn("Pipeline", result)
        self.assertIn("Pilot", result)

    def test_report_event(self):
        result = self._compose_str(
            RenderEvent(EventKind.REPORT, {"title": "Summary", "rows": [("Tasks", "3/5")]})
        )
        self.assertIn("Summary", result)
        self.assertIn("Tasks", result)
        self.assertIn("3/5", result)

    def test_activity_event_info(self):
        result = self._compose_str(
            RenderEvent(EventKind.ACTIVITY, {"msg": "[Architect] Decomposing", "level": "INFO"})
        )
        self.assertIn("[Architect]", result)

    def test_activity_event_error(self):
        result = self._compose_str(
            RenderEvent(EventKind.ACTIVITY, {"msg": "Something failed", "level": "ERROR"})
        )
        self.assertIn("Something failed", result)

    def test_banner_contains_lao(self):
        result = self._compose_str(RenderEvent(EventKind.BANNER, {}))
        self.assertIn("LAO", result)

    def test_model_swap_mini_bar_varies_with_tick(self):
        a = _model_swap_mini_bar(0)
        b = _model_swap_mini_bar(3)
        self.assertTrue(a.startswith("[") and a.endswith("]"))
        self.assertTrue(b.startswith("[") and b.endswith("]"))

    def test_model_swap_mini_bar_html_is_markup(self):
        h = _model_swap_mini_bar_html(1)
        self.assertIn("[", h)
        self.assertIn("]", h)

    def test_ansi_in_content_is_stripped(self):
        result = self._compose_str(
            RenderEvent(EventKind.USER_MSG, {"content": "\x1b[35mcolored\x1b[0m"})
        )
        self.assertNotIn("\x1b", result)


class TestViewComposerRich(unittest.TestCase):
    """Smoke-test that rich mode produces Rich renderables without crashing."""

    def setUp(self):
        self.composer = ViewComposer(_rich_caps())

    def test_info_returns_text_object(self):
        from rich.text import Text
        items = self.composer.compose(RenderEvent(EventKind.INFO, {"msg": "hello"}))
        self.assertTrue(any(isinstance(i, Text) for i in items))

    def test_report_returns_table(self):
        from rich.table import Table
        items = self.composer.compose(
            RenderEvent(EventKind.REPORT, {"title": "T", "rows": [("a", "b")]})
        )
        self.assertTrue(any(isinstance(i, Table) for i in items))

    def test_transition_returns_rule(self):
        from rich.rule import Rule
        items = self.composer.compose(
            RenderEvent(EventKind.TRANSITION, {"from_mode": "A", "to_mode": "B"})
        )
        self.assertTrue(any(isinstance(i, Rule) for i in items))


# ─────────────────────────────────────────────────────────────────────────────
# SlashCommandCompleter
# ─────────────────────────────────────────────────────────────────────────────

class TestSlashCommandCompleter(unittest.TestCase):
    def test_completes_slash_prefix(self):
        completer = SlashCommandCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/he"
        completions = list(completer.get_completions(doc, MagicMock()))
        self.assertIn("/help", [c.text for c in completions])

    def test_no_completions_without_slash(self):
        completer = SlashCommandCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "hello"
        self.assertEqual(list(completer.get_completions(doc, MagicMock())), [])

    def test_all_commands_present(self):
        completer = SlashCommandCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/"
        texts = [c.text for c in completer.get_completions(doc, MagicMock())]
        for cmd in ("/help", "/status", "/resume", "/clear", "/exit", "/quit"):
            self.assertIn(cmd, texts)


# ─────────────────────────────────────────────────────────────────────────────
# UnifiedUI — basic lifecycle and status
# ─────────────────────────────────────────────────────────────────────────────

class TestUnifiedUIBasic(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)

    def tearDown(self):
        from local_ai_agent_orchestrator import unified_ui
        unified_ui._active_ui = None
        reset_settings_for_tests()
        self._td.cleanup()

    def test_get_unified_ui_none_by_default(self):
        from local_ai_agent_orchestrator import unified_ui
        unified_ui._active_ui = None
        self.assertIsNone(get_unified_ui())

    def test_instantiation_sets_active_ui(self):
        ui = UnifiedUI()
        self.assertIs(get_unified_ui(), ui)

    def test_update_status_fields(self):
        ui = UnifiedUI()
        ui.update_status(phase="Coder", task="test task", model="test-model")
        self.assertEqual(ui._phase, "Coder")
        self.assertEqual(ui._task, "test task")
        self.assertEqual(ui._model_line, "test-model")

    def test_update_status_thread_safe(self):
        ui = UnifiedUI()
        errors: list[Exception] = []

        def writer(phase: str) -> None:
            try:
                for i in range(50):
                    ui.update_status(phase=f"{phase}-{i}", task=f"task-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"p{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_apply_runner_context_updates_ui(self):
        ui = UnifiedUI()
        apply_runner_context(phase="Architect", task="decompose")
        self.assertEqual(ui._phase, "Architect")
        self.assertEqual(ui._task, "decompose")

    def test_apply_runner_context_no_active_ui(self):
        from local_ai_agent_orchestrator import unified_ui
        unified_ui._active_ui = None
        # Must not raise
        apply_runner_context(phase="Coder")

    def test_show_methods_do_not_raise(self):
        ui = UnifiedUI()
        ui.show_info("info message")
        ui.show_error("error message", suggestion="fix it")
        ui.show_user_message("hello")
        ui.show_assistant_message("hi there")
        ui.show_tool_call("list_dir", {"path": "."})
        ui.show_tool_result("list_dir", "src/\ntests/")
        ui.show_usage(100, 50)
        ui.show_thinking("thinking hard")
        ui.show_transition("Pipeline", "Pilot")
        ui.show_report("Summary", [("Tasks", "3/5")])
        ui.log_activity("[Architect] Decomposing plan")

    def test_toggle_activity_detail_is_noop_facade(self):
        ui = UnifiedUI()
        # Should not raise; delegates to shell key binding
        ui.toggle_activity_detail()

    def test_supports_color_property(self):
        ui = UnifiedUI()
        # Must be a bool
        self.assertIsInstance(ui._supports_color, bool)


# ─────────────────────────────────────────────────────────────────────────────
# UnifiedUI — queue-aware reports
# ─────────────────────────────────────────────────────────────────────────────

class TestUnifiedUIReports(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)
        self.queue = TaskQueue(db_path=self.td / ".lao" / "state.db")

    def tearDown(self):
        from local_ai_agent_orchestrator import unified_ui
        unified_ui._active_ui = None
        reset_settings_for_tests()
        self._td.cleanup()

    def test_build_idle_report_with_tasks(self):
        ui = UnifiedUI()
        ui.set_queue_getter(lambda: self.queue)
        plan_id = self.queue.register_plan("rpt.md", "# Report\ncontent")
        self.queue.add_tasks(plan_id, [
            {"title": "A", "description": "D", "file_paths": [], "dependencies": []},
            {"title": "B", "description": "D", "file_paths": [], "dependencies": []},
        ])
        task = self.queue.next_pending()
        self.queue.mark_coding(task.id)
        self.queue.mark_coded(task.id, "output")
        self.queue.mark_review(task.id)
        self.queue.mark_completed(task.id)

        rows = ui.build_idle_report()
        labels = [r[0] for r in rows]
        self.assertIn("Tasks", labels)
        self.assertIn("Plan", labels)

    def test_build_idle_report_empty_queue(self):
        ui = UnifiedUI()
        ui.set_queue_getter(lambda: self.queue)
        self.assertEqual(ui.build_idle_report(), [])

    def test_build_resume_report(self):
        ui = UnifiedUI()
        ui.set_queue_getter(lambda: self.queue)
        plan_id = self.queue.register_plan("res.md", "# Resume\ncontent")
        self.queue.add_tasks(plan_id, [
            {"title": "X", "description": "D", "file_paths": [], "dependencies": []},
        ])
        rows = ui.build_resume_report()
        labels = [r[0] for r in rows]
        self.assertIn("Pending", labels)

    def test_snapshot_stats(self):
        ui = UnifiedUI()
        ui.set_queue_getter(lambda: self.queue)
        plan_id = self.queue.register_plan("snap.md", "# Snap\ncontent")
        self.queue.add_tasks(plan_id, [
            {"title": "S", "description": "D", "file_paths": [], "dependencies": []},
        ])
        ui.snapshot_stats()
        self.assertIn("pending", ui._last_stats_snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# LogBridge routing
# ─────────────────────────────────────────────────────────────────────────────

class TestLogBridge(unittest.TestCase):
    """Test that LogBridge correctly routes log records into the RenderBus."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)

    def tearDown(self):
        from local_ai_agent_orchestrator import unified_ui
        unified_ui._active_ui = None
        reset_settings_for_tests()
        self._td.cleanup()

    def _make_ui_with_captured_bus(self):
        ui = UnifiedUI()
        captured: list[RenderEvent] = []

        def _consumer(ev: RenderEvent) -> None:
            captured.append(ev)
            # Also let the shell handle status-affecting events
            ui._shell._handle_event(ev)

        ui._bus.set_consumer(_consumer)
        return ui, captured

    def _emit(self, ui: UnifiedUI, msg: str, level: str = "INFO") -> None:
        from local_ai_agent_orchestrator.unified_ui import LogBridge
        if ui._log_bridge is None:
            ui._log_bridge = LogBridge(ui._bus, ui._shell)
        record = logging.LogRecord(
            name="test", level=getattr(logging, level),
            pathname="", lineno=0, msg=msg, args=(), exc_info=None,
        )
        ui._log_bridge.emit(record)

    def test_http_request_dropped(self):
        ui, captured = self._make_ui_with_captured_bus()
        self._emit(ui, "HTTP Request: GET /v1/models")
        activity = [e for e in captured if e.kind == EventKind.ACTIVITY]
        self.assertEqual(activity, [])

    def test_separator_line_dropped(self):
        ui, captured = self._make_ui_with_captured_bus()
        self._emit(ui, "=" * 60)
        activity = [e for e in captured if e.kind == EventKind.ACTIVITY]
        self.assertEqual(activity, [])

    def test_architect_line_emits_activity(self):
        ui, captured = self._make_ui_with_captured_bus()
        self._emit(ui, "[Architect] Decomposing plan")
        activity = [e for e in captured if e.kind == EventKind.ACTIVITY]
        self.assertEqual(len(activity), 1)
        self.assertIn("[Architect]", activity[0].payload["msg"])

    def test_architect_line_updates_phase(self):
        ui, _ = self._make_ui_with_captured_bus()
        self._emit(ui, "[Architect] Decomposing plan")
        self.assertEqual(ui._shell._phase, "Architect")

    def test_coder_task_line_emits_activity(self):
        ui, captured = self._make_ui_with_captured_bus()
        self._emit(ui, "Coding task #1: Build UI (attempt 1/3)")
        activity = [e for e in captured if e.kind == EventKind.ACTIVITY]
        self.assertEqual(len(activity), 1)

    def test_coder_task_line_updates_phase_and_task(self):
        ui, _ = self._make_ui_with_captured_bus()
        self._emit(ui, "Coding task #1: Build UI (attempt 1/3)")
        self.assertEqual(ui._shell._phase, "Coder")
        self.assertIn("#1", ui._shell._task)

    def test_memory_gate_waiting_updates_memory(self):
        ui, _ = self._make_ui_with_captured_bus()
        self._emit(ui, "[MemoryGate] Waiting... available=6.5GB target=19.5GB")
        self.assertIn("6.5GB", ui._shell._memory_line)

    def test_memory_gate_cleared_clears_memory(self):
        ui, _ = self._make_ui_with_captured_bus()
        ui._shell._memory_line = "settling..."
        self._emit(ui, "[MemoryGate] Pages cleared after 2s")
        self.assertEqual(ui._shell._memory_line, "")

    def test_warning_level_surfaces(self):
        ui, captured = self._make_ui_with_captured_bus()
        self._emit(ui, "Something went wrong", level="WARNING")
        activity = [e for e in captured if e.kind == EventKind.ACTIVITY]
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0].payload["level"], "WARNING")

    def test_error_level_surfaces(self):
        ui, captured = self._make_ui_with_captured_bus()
        self._emit(ui, "Fatal error occurred", level="ERROR")
        activity = [e for e in captured if e.kind == EventKind.ACTIVITY]
        self.assertEqual(len(activity), 1)


if __name__ == "__main__":
    unittest.main()
