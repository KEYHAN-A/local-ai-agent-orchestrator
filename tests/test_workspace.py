# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for per-plan workspaces and .lao layout."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.tools import _workspace_root, use_plan_workspace


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class TestPerPlanWorkspace(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_workspace_for_plan_stem(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")

            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("IOS_DEV_PLAN.md", "hello plan body unique")
            expected = (root / ".lao" / "workspaces" / "IOS_DEV_PLAN").resolve()
            self.assertEqual(q.workspace_for_plan(pid), expected)
            self.assertTrue(expected.is_dir())

    def test_use_plan_workspace_context(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")

            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("Foo.md", "other content")
            path = q.workspace_for_plan(pid)
            with use_plan_workspace(q, pid):
                self.assertEqual(_workspace_root(), path)


if __name__ == "__main__":
    unittest.main()
