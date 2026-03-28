# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for prompt builders."""

import unittest

from local_ai_agent_orchestrator.prompts import build_reviewer_messages
from local_ai_agent_orchestrator.state import MicroTask


class TestReviewerRubric(unittest.TestCase):
    def test_api_task_gets_extra_hints(self):
        task = MicroTask(
            id=1,
            plan_id="p",
            title="Add REST API handler",
            description="Expose HTTP endpoint for search",
        )
        msgs = build_reviewer_messages(task, "print('x')")
        user = msgs[-1]["content"]
        self.assertIn("Task-specific review hints", user)
        self.assertIn("request/response", user.lower())

    def test_generic_task_has_no_rubric_section(self):
        task = MicroTask(
            id=2,
            plan_id="p",
            title="Rename variable",
            description="Cosmetic cleanup in utils",
        )
        msgs = build_reviewer_messages(task, "x = 1")
        user = msgs[-1]["content"]
        self.assertNotIn("Task-specific review hints", user)


if __name__ == "__main__":
    unittest.main()
