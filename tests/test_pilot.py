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

    def test_unknown_command_passes_through(self):
        """Unknown /commands now pass through to the LLM instead of erroring."""
        agent, messages = self._make_agent()
        result = agent._handle_slash_command("/foobar")
        self.assertIsNone(result)
        self.assertEqual(len(messages), 0)

    def test_absolute_path_passes_through(self):
        """Absolute paths like /Users/keyhan/... should NOT be treated as slash commands."""
        agent, messages = self._make_agent()
        result = agent._handle_slash_command("/Users/keyhan/projects/benchmark")
        self.assertIsNone(result)
        self.assertEqual(len(messages), 0)

    def test_unix_path_passes_through(self):
        agent, _ = self._make_agent()
        result = agent._handle_slash_command("/tmp/somefile.txt")
        self.assertIsNone(result)

    def test_non_slash_returns_none(self):
        agent, _ = self._make_agent()
        result = agent._handle_slash_command("hello world")
        self.assertIsNone(result)

    def test_project_command_recognized(self):
        agent, messages = self._make_agent()
        result = agent._handle_slash_command("/project list")
        self.assertIsNone(result)
        self.assertTrue(len(messages) >= 1)

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


class TestPilotLlmCallbacks(unittest.TestCase):
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

    def test_tool_loop_calls_llm_begin_end(self):
        events: list = []
        agent = PilotAgent(
            self.mm,
            self.queue,
            on_assistant_message=lambda _m: None,
            on_llm_round_begin=lambda h: events.append(("begin", h)),
            on_llm_round_end=lambda: events.append(("end",)),
        )
        agent._history.append({"role": "user", "content": "hi"})

        mock_msg = MagicMock()
        mock_msg.content = "reply"
        mock_msg.tool_calls = None
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage = None
        client = MagicMock()
        client.chat.completions.create.return_value = mock_resp

        from local_ai_agent_orchestrator.settings import get_settings

        cfg = get_settings().models["pilot"]
        out = agent._tool_loop(client, "test-model", cfg)
        self.assertEqual(out, "reply")
        self.assertEqual([e[0] for e in events], ["begin", "end"])
        self.assertIn("workspace", events[0][1])

    def test_tool_loop_calls_tool_begin(self):
        events: list = []
        agent = PilotAgent(
            self.mm,
            self.queue,
            on_assistant_message=lambda _m: None,
            on_tool_round_begin=lambda n: events.append(("tool", n)),
            on_llm_round_begin=lambda _h: None,
            on_llm_round_end=lambda: None,
        )
        agent._history.append({"role": "user", "content": "hi"})

        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "pipeline_status"
        tc.function.arguments = "{}"
        mock_msg = MagicMock()
        mock_msg.content = None
        mock_msg.tool_calls = [tc]
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        resp1 = MagicMock()
        resp1.choices = [mock_choice]
        resp1.usage = None

        mock_msg2 = MagicMock()
        mock_msg2.content = "done"
        mock_msg2.tool_calls = None
        mock_choice2 = MagicMock()
        mock_choice2.message = mock_msg2
        resp2 = MagicMock()
        resp2.choices = [mock_choice2]
        resp2.usage = None

        client = MagicMock()
        client.chat.completions.create.side_effect = [resp1, resp2]

        from local_ai_agent_orchestrator.settings import get_settings

        cfg = get_settings().models["pilot"]
        agent._tool_loop(client, "test-model", cfg)
        self.assertTrue(any(e == ("tool", "pipeline_status") for e in events))


class TestPilotBudgetGuard(unittest.TestCase):
    """Verify that 4 consecutive tool errors triggers bail-out."""

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

    def test_bails_after_four_errors(self):
        messages_out: list = []
        agent = PilotAgent(
            self.mm,
            self.queue,
            on_assistant_message=lambda m: messages_out.append(m),
            on_llm_round_begin=lambda _h: None,
            on_llm_round_end=lambda: None,
        )
        agent._history.append({"role": "user", "content": "find something"})

        # Build a response with 4 tool calls that all return ERROR
        tool_calls = []
        for i in range(4):
            tc = MagicMock()
            tc.id = f"call_{i}"
            tc.function.name = "list_dir"
            tc.function.arguments = '{"path": "/nonexistent"}'
            tool_calls.append(tc)

        mock_msg = MagicMock()
        mock_msg.content = None
        mock_msg.tool_calls = tool_calls
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        resp = MagicMock()
        resp.choices = [mock_choice]
        resp.usage = None

        client = MagicMock()
        client.chat.completions.create.return_value = resp

        from local_ai_agent_orchestrator.settings import get_settings
        cfg = get_settings().models["pilot"]

        result = agent._tool_loop(client, "test-model", cfg)
        self.assertIn("several errors", result)
        self.assertTrue(len(messages_out) >= 1)

    def test_resets_on_success(self):
        """A successful tool call resets the consecutive error counter."""
        messages_out: list = []
        agent = PilotAgent(
            self.mm,
            self.queue,
            on_assistant_message=lambda m: messages_out.append(m),
            on_llm_round_begin=lambda _h: None,
            on_llm_round_end=lambda: None,
        )
        agent._history.append({"role": "user", "content": "status"})

        # 3 errors then 1 success — should NOT bail
        tc_err1 = MagicMock()
        tc_err1.id = "call_e1"
        tc_err1.function.name = "list_dir"
        tc_err1.function.arguments = '{"path": "/nope"}'
        tc_err2 = MagicMock()
        tc_err2.id = "call_e2"
        tc_err2.function.name = "list_dir"
        tc_err2.function.arguments = '{"path": "/nope2"}'
        tc_err3 = MagicMock()
        tc_err3.id = "call_e3"
        tc_err3.function.name = "list_dir"
        tc_err3.function.arguments = '{"path": "/nope3"}'
        tc_ok = MagicMock()
        tc_ok.id = "call_ok"
        tc_ok.function.name = "pipeline_status"
        tc_ok.function.arguments = "{}"

        mock_msg1 = MagicMock()
        mock_msg1.content = None
        mock_msg1.tool_calls = [tc_err1, tc_err2, tc_err3, tc_ok]
        mock_choice1 = MagicMock()
        mock_choice1.message = mock_msg1
        resp1 = MagicMock()
        resp1.choices = [mock_choice1]
        resp1.usage = None

        mock_msg2 = MagicMock()
        mock_msg2.content = "done"
        mock_msg2.tool_calls = None
        mock_choice2 = MagicMock()
        mock_choice2.message = mock_msg2
        resp2 = MagicMock()
        resp2.choices = [mock_choice2]
        resp2.usage = None

        client = MagicMock()
        client.chat.completions.create.side_effect = [resp1, resp2]

        from local_ai_agent_orchestrator.settings import get_settings
        cfg = get_settings().models["pilot"]

        result = agent._tool_loop(client, "test-model", cfg)
        self.assertEqual(result, "done")


class TestPilotIntentDetection(unittest.TestCase):
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

    def _agent(self):
        return PilotAgent(self.mm, self.queue)

    def test_detects_absolute_path(self):
        agent = self._agent()
        result = agent._detect_project_intent("/Users/keyhan/projects/benchmark")
        self.assertEqual(result, "/Users/keyhan/projects/benchmark")

    def test_detects_continue_pattern(self):
        agent = self._agent()
        result = agent._detect_project_intent("continue working on benchmark project")
        self.assertEqual(result, "benchmark")

    def test_detects_resume_pattern(self):
        agent = self._agent()
        result = agent._detect_project_intent("resume the myapp project")
        self.assertEqual(result, "myapp")

    def test_detects_check_pattern(self):
        agent = self._agent()
        result = agent._detect_project_intent("check benchmark project")
        self.assertEqual(result, "benchmark")

    def test_no_intent_for_regular_message(self):
        agent = self._agent()
        result = agent._detect_project_intent("what is the weather like?")
        self.assertIsNone(result)

    def test_no_intent_for_simple_slash(self):
        """A single / without a path should not trigger intent detection."""
        agent = self._agent()
        result = agent._detect_project_intent("/")
        self.assertIsNone(result)


class TestPilotFallbackResponse(unittest.TestCase):
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

    def test_fallback_mentions_no_config(self):
        agent = PilotAgent(self.mm, self.queue)
        msg = agent._build_fallback_response()
        self.assertIn("No factory.yaml", msg)
        self.assertIn("task queue is empty", msg.lower())

    def test_fallback_with_config(self):
        (self.td / "factory.yaml").write_text("test: true")
        agent = PilotAgent(self.mm, self.queue)
        msg = agent._build_fallback_response()
        self.assertNotIn("No factory.yaml", msg)


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
