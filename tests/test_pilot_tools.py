# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for pilot-mode tools."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests


class TestPilotToolSchemas(unittest.TestCase):
    def test_schemas_and_dispatch_aligned(self):
        from local_ai_agent_orchestrator.pilot_tools import (
            PILOT_TOOL_SCHEMAS,
            PILOT_TOOL_DISPATCH,
        )
        schema_names = {s["function"]["name"] for s in PILOT_TOOL_SCHEMAS}
        dispatch_names = set(PILOT_TOOL_DISPATCH.keys())
        self.assertEqual(schema_names, dispatch_names)

    def test_pilot_tools_superset_of_base_tools(self):
        from local_ai_agent_orchestrator.tools import TOOL_SCHEMAS
        from local_ai_agent_orchestrator.pilot_tools import PILOT_TOOL_SCHEMAS
        base_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        pilot_names = {s["function"]["name"] for s in PILOT_TOOL_SCHEMAS}
        self.assertTrue(base_names.issubset(pilot_names))

    def test_pilot_specific_tools_present(self):
        from local_ai_agent_orchestrator.pilot_tools import PILOT_TOOL_SCHEMAS
        names = {s["function"]["name"] for s in PILOT_TOOL_SCHEMAS}
        for expected in (
            "create_plan",
            "pipeline_status",
            "retry_failed",
            "resume_pipeline",
            "codebase_search",
            "gate_summary",
        ):
            self.assertIn(expected, names)


class TestCreatePlan(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)

    def tearDown(self):
        reset_settings_for_tests()
        self._td.cleanup()

    def test_creates_plan_file(self):
        from local_ai_agent_orchestrator.pilot_tools import create_plan
        result = create_plan("My Feature", "# My Feature\n\nImplement X.")
        self.assertIn("OK", result)
        plan_file = self.td / "plans" / "My_Feature.md"
        self.assertTrue(plan_file.exists())
        self.assertIn("# My Feature", plan_file.read_text())

    def test_rejects_duplicate_title(self):
        from local_ai_agent_orchestrator.pilot_tools import create_plan
        create_plan("Dup", "content")
        result = create_plan("Dup", "content 2")
        self.assertIn("ERROR", result)

    def test_rejects_empty_title(self):
        from local_ai_agent_orchestrator.pilot_tools import create_plan
        result = create_plan("!!!", "content")
        self.assertIn("ERROR", result)

    def test_strips_md_suffix_from_title(self):
        from local_ai_agent_orchestrator.pilot_tools import create_plan
        result = create_plan("FIX_AUTH_MANAGER.md", "# Fix\n")
        self.assertIn("OK", result)
        plan_file = self.td / "plans" / "FIX_AUTH_MANAGER.md"
        self.assertTrue(plan_file.exists())


class TestPipelineStatus(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)

    def tearDown(self):
        from local_ai_agent_orchestrator.pilot_tools import bind_queue as _bind
        _bind(None)
        reset_settings_for_tests()
        self._td.cleanup()

    def test_status_without_queue(self):
        from local_ai_agent_orchestrator.pilot_tools import bind_queue, pipeline_status
        bind_queue(None)
        result = pipeline_status()
        self.assertIn("ERROR", result)

    def test_status_with_empty_queue(self):
        from local_ai_agent_orchestrator.pilot_tools import (
            bind_queue,
            pipeline_status,
        )
        from local_ai_agent_orchestrator.state import TaskQueue
        q = TaskQueue(db_path=self.td / ".lao" / "state.db")
        bind_queue(q)
        result = pipeline_status()
        self.assertIn("Pipeline Status", result)
        self.assertIn("No tasks", result)

    def test_status_with_tasks(self):
        from local_ai_agent_orchestrator.pilot_tools import (
            bind_queue,
            pipeline_status,
        )
        from local_ai_agent_orchestrator.state import TaskQueue
        q = TaskQueue(db_path=self.td / ".lao" / "state.db")
        plan_id = q.register_plan("test.md", "# Test\ncontent")
        q.add_tasks(plan_id, [
            {"title": "T1", "description": "D1", "file_paths": [], "dependencies": []},
            {"title": "T2", "description": "D2", "file_paths": [], "dependencies": []},
        ])
        bind_queue(q)
        result = pipeline_status()
        self.assertIn("pending: 2", result)
        self.assertIn("test.md", result)
        self.assertIn("workspace=", result)
        self.assertIn("id=", result)

    def test_gate_summary_with_queue(self):
        from local_ai_agent_orchestrator.pilot_tools import bind_queue, gate_summary
        from local_ai_agent_orchestrator.state import TaskQueue

        q = TaskQueue(db_path=self.td / ".lao" / "state.db")
        bind_queue(q)
        try:
            text = gate_summary()
            self.assertIn("Validation gates", text)
            self.assertIn("infer_validation_commands", text)
        finally:
            bind_queue(None)


class TestRetryFailed(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "plans").mkdir()
        (self.td / ".lao").mkdir()
        init_settings(cwd=self.td)

    def tearDown(self):
        from local_ai_agent_orchestrator.pilot_tools import bind_queue as _bind
        _bind(None)
        reset_settings_for_tests()
        self._td.cleanup()

    def test_retry_with_no_failures(self):
        from local_ai_agent_orchestrator.pilot_tools import bind_queue, retry_failed
        from local_ai_agent_orchestrator.state import TaskQueue
        q = TaskQueue(db_path=self.td / ".lao" / "state.db")
        bind_queue(q)
        result = retry_failed()
        self.assertIn("No failed", result)

    def test_retry_resets_failed_tasks(self):
        from local_ai_agent_orchestrator.pilot_tools import bind_queue, retry_failed
        from local_ai_agent_orchestrator.state import TaskQueue
        q = TaskQueue(db_path=self.td / ".lao" / "state.db")
        plan_id = q.register_plan("retry.md", "# Retry\ncontent")
        q.add_tasks(plan_id, [
            {"title": "T1", "description": "D1", "file_paths": [], "dependencies": []},
        ])
        task = q.next_pending()
        q.mark_failed(task.id, "Test error")
        bind_queue(q)
        result = retry_failed()
        self.assertIn("OK", result)
        self.assertIn("1", result)
        task = q.get_task(task.id)
        self.assertEqual(task.status, "pending")

    def test_retry_scoped_by_plan_filename(self):
        from local_ai_agent_orchestrator.pilot_tools import bind_queue, retry_failed
        from local_ai_agent_orchestrator.state import TaskQueue
        q = TaskQueue(db_path=self.td / ".lao" / "state.db")
        plan_id = q.register_plan("scoped.md", "# Scoped\ncontent")
        q.add_tasks(plan_id, [
            {"title": "T1", "description": "D1", "file_paths": [], "dependencies": []},
        ])
        task = q.next_pending()
        q.mark_failed(task.id, "Test error")
        bind_queue(q)
        result = retry_failed(plan_id="scoped.md")
        self.assertIn("OK", result)
        self.assertEqual(q.get_task(task.id).status, "pending")

    def test_retry_unknown_plan_ref_errors(self):
        from local_ai_agent_orchestrator.pilot_tools import bind_queue, retry_failed
        from local_ai_agent_orchestrator.state import TaskQueue
        q = TaskQueue(db_path=self.td / ".lao" / "state.db")
        bind_queue(q)
        result = retry_failed(plan_id="does-not-exist.md")
        self.assertIn("ERROR", result)


class TestResumePipeline(unittest.TestCase):
    def test_sets_resume_flag(self):
        from local_ai_agent_orchestrator.pilot_tools import (
            is_resume_requested,
            reset_resume_flag,
            resume_pipeline,
        )
        reset_resume_flag()
        self.assertFalse(is_resume_requested())
        result = resume_pipeline()
        self.assertIn("OK", result)
        self.assertTrue(is_resume_requested())
        reset_resume_flag()


if __name__ == "__main__":
    unittest.main()
