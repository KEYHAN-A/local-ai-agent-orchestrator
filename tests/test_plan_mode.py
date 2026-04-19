# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan-mode lifecycle and permission interaction."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
import importlib

from local_ai_agent_orchestrator import permissions
from local_ai_agent_orchestrator.tools.base import get
from local_ai_agent_orchestrator.tools.meta import is_plan_mode, set_plan_mode

plan_mode = importlib.import_module("local_ai_agent_orchestrator.tools.plan_mode")


YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
permissions:
  mode: auto
  allow: []
  deny: []
"""


def _init(td: Path):
    (td / ".lao").mkdir(parents=True, exist_ok=True)
    (td / "plans").mkdir(parents=True, exist_ok=True)
    cfg = td / "factory.yaml"
    cfg.write_text(YAML.strip(), encoding="utf-8")
    init_settings(config_path=cfg, cwd=td)


class TestPlanMode(unittest.TestCase):
    def tearDown(self):
        try:
            set_plan_mode(False)
        except Exception:
            pass
        reset_settings_for_tests()

    def test_enter_then_exit_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            self.assertFalse(is_plan_mode())
            r = plan_mode.enter_plan_mode("ambiguous request")
            self.assertIn("Plan mode active", r)
            self.assertTrue(is_plan_mode())
            denied = plan_mode.exit_plan_mode("plan text", approved=False)
            self.assertTrue(denied.startswith("ERROR"))
            self.assertTrue(is_plan_mode())
            ok = plan_mode.exit_plan_mode("plan text", approved=True)
            self.assertIn("re-enabled", ok)
            self.assertFalse(is_plan_mode())

    def test_writes_blocked_during_plan_mode(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            plan_mode.enter_plan_mode("design")
            try:
                d = permissions.evaluate(get("file_write"), {"path": "x.txt", "content": "y"})
                self.assertFalse(d.granted)
                read_ok = permissions.evaluate(get("file_read"), {"path": "x.txt"})
                self.assertTrue(read_ok.granted)
            finally:
                plan_mode.exit_plan_mode("ok", approved=True)


if __name__ == "__main__":
    unittest.main()
