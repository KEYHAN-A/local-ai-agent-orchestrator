# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for PilotAgent class and conversation logic."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from local_ai_agent_orchestrator.pilot import PilotAgent, PilotResult
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


class TestPilotResult(unittest.TestCase):
    def test_enum_values(self):
        self.assertEqual(PilotResult.CONTINUE.name, "CONTINUE")
        self.assertEqual(PilotResult.RESUME_PIPELINE.name, "RESUME_PIPELINE")
        self.assertEqual(PilotResult.EXIT.name, "EXIT")


class TestPilotAgentSlashCommands(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)
        self.queue = TaskQueue(db_path=self.td / ".lao" / "state.db")
        self.mm = MagicMock()
        self.mm.ensure_loaded.return_value = "test-model"

    def tearDown(self):
        reset_settings_for_tests()
        self._td.cleanup()

    def _make_agent(self):
        messages = []
        agent = PilotAgent(
            self.mm, self.queue,
            on_assistant_message=lambda m: messages.append(m),
        )
        return agent, messages

    def test_exit_command(self):
        agent, _ = self._make_agent()
        result = agent._handle_slash_command("/exit")
        self.assertEqual(result, PilotResult.EXIT)

    def test_quit_command(self):
        agent, _ = self._make_agent()
        result = agent._handle_slash_command("/quit")
        self.assertEqual(result, PilotResult.EXIT)

    def test_resume_command(self):
        agent, _ = self._make_agent()
        result = agent._handle_slash_command("/resume")
        self.assertEqual(result, PilotResult.RESUME_PIPELINE)

    def test_continue_command(self):
        agent, _ = self._make_agent()
        result = agent._handle_slash_command("/continue")
        self.assertEqual(result, PilotResult.RESUME_PIPELINE)

    def test_go_command(self):
        agent, _ = self._make_agent()
        result = agent._handle_slash_command("/go")
        self.assertEqual(result, PilotResult.RESUME_PIPELINE)

    def test_clear_command(self):
        agent, messages = self._make_agent()
        agent._history.append({"role": "user", "content": "hello"})
        result = agent._handle_slash_command("/clear")
        self.assertIsNone(result)
        self.assertEqual(len(agent._history), 0)
        self.assertEqual(len(messages), 1)
        self.assertIn("cleared", messages[0].lower())

    def test_help_command(self):
        agent, messages = self._make_agent()
        result = agent._handle_slash_command("/help")
        self.assertIsNone(result)
        self.assertEqual(len(messages), 1)
        self.assertIn("/status", messages[0])
        self.assertIn("/resume", messages[0])

    def test_status_command(self):
        agent, messages = self._make_agent()
        result = agent._handle_slash_command("/status")
        self.assertIsNone(result)
        self.assertEqual(len(messages), 1)
        self.assertIn("Pipeline Status", messages[0])

    def test_unknown_command(self):
        agent, messages = self._make_agent()
        result = agent._handle_slash_command("/foobar")
        self.assertIsNone(result)
        self.assertIn("Unknown command", messages[0])

    def test_non_slash_returns_none(self):
        agent, _ = self._make_agent()
        result = agent._handle_slash_command("hello world")
        self.assertIsNone(result)

    def test_run_exits_on_none_input(self):
        agent, _ = self._make_agent()
        inputs = iter([None])
        result = agent.run(get_input=lambda: next(inputs))
        self.assertEqual(result, PilotResult.EXIT)

    def test_run_exits_on_slash_exit(self):
        agent, _ = self._make_agent()
        inputs = iter(["/exit"])
        result = agent.run(get_input=lambda: next(inputs))
        self.assertEqual(result, PilotResult.EXIT)

    def test_run_resumes_on_slash_resume(self):
        agent, _ = self._make_agent()
        inputs = iter(["/resume"])
        result = agent.run(get_input=lambda: next(inputs))
        self.assertEqual(result, PilotResult.RESUME_PIPELINE)

    def test_run_skips_empty_input(self):
        agent, _ = self._make_agent()
        inputs = iter(["", "  ", "/exit"])
        result = agent.run(get_input=lambda: next(inputs))
        self.assertEqual(result, PilotResult.EXIT)


class TestPilotAgentContext(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)
        self.queue = TaskQueue(db_path=self.td / ".lao" / "state.db")
        self.mm = MagicMock()

    def tearDown(self):
        reset_settings_for_tests()
        self._td.cleanup()

    def test_build_context_includes_workspace(self):
        agent = PilotAgent(self.mm, self.queue)
        ctx = agent._build_context()
        self.assertIn("Workspace:", ctx)
        self.assertIn("Plans directory:", ctx)
        self.assertIn("Task queue:", ctx)

    def test_build_context_includes_plan_info(self):
        plan_id = self.queue.register_plan("ctx.md", "# Context\ncontent")
        self.queue.add_tasks(plan_id, [
            {"title": "A", "description": "D", "file_paths": [], "dependencies": []},
        ])
        agent = PilotAgent(self.mm, self.queue)
        ctx = agent._build_context()
        self.assertIn("ctx.md", ctx)

    def test_build_context_includes_failed_details(self):
        plan_id = self.queue.register_plan("fail.md", "# Fail\ncontent")
        self.queue.add_tasks(plan_id, [
            {"title": "FailTask", "description": "D", "file_paths": [], "dependencies": []},
        ])
        task = self.queue.next_pending()
        self.queue.mark_failed(task.id, "Test failure", escalation_reason="test_error")
        agent = PilotAgent(self.mm, self.queue)
        ctx = agent._build_context()
        self.assertIn("FAILED", ctx)
        self.assertIn("FailTask", ctx)
        self.assertIn("test_error", ctx)


class TestPilotConversationPersistence(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)
        self.queue = TaskQueue(db_path=self.td / ".lao" / "state.db")

    def tearDown(self):
        reset_settings_for_tests()
        self._td.cleanup()

    def test_log_and_retrieve_messages(self):
        self.queue.start_new_pilot_session()
        self.queue.log_pilot_message("user", "Hello!")
        self.queue.log_pilot_message("assistant", "Hi there!")
        history = self.queue.get_pilot_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

    def test_clear_session(self):
        self.queue.start_new_pilot_session()
        self.queue.log_pilot_message("user", "test")
        self.queue.clear_pilot_session()
        self.queue.start_new_pilot_session()
        history = self.queue.get_pilot_history()
        self.assertEqual(len(history), 0)

    def test_new_session_is_isolated(self):
        s1 = self.queue.start_new_pilot_session()
        self.queue.log_pilot_message("user", "session 1")
        s2 = self.queue.start_new_pilot_session()
        self.assertNotEqual(s1, s2)
        history = self.queue.get_pilot_history()
        self.assertEqual(len(history), 0)


if __name__ == "__main__":
    unittest.main()
