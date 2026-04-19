# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-project / user-global LAO memory."""

import os
import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.services import memory


YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


def _init(td: Path):
    (td / ".lao").mkdir(parents=True, exist_ok=True)
    (td / "plans").mkdir(parents=True, exist_ok=True)
    cfg = td / "factory.yaml"
    cfg.write_text(YAML.strip(), encoding="utf-8")
    init_settings(config_path=cfg, cwd=td)


class TestMemory(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()
        os.environ.pop("LAO_FAKE_HOME", None)

    def test_append_then_read_returns_block(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _init(td_path)
            ok = memory.append_fact("build cmd: pytest -q", scope="project", source="cli")
            self.assertTrue(ok)
            block = memory.read_memory_block()
            self.assertIn("LAO Project Memory", (td_path / "LAO_MEMORY.md").read_text())
            self.assertIn("build cmd: pytest -q", block)

    def test_append_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            self.assertTrue(memory.append_fact("note: x", scope="project"))
            self.assertFalse(memory.append_fact("note: x", scope="project"))

    def test_forget_removes_matching_lines(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            memory.append_fact("keep me", scope="project")
            memory.append_fact("remove me please", scope="project")
            removed = memory.forget_fact("remove me", scope="project")
            self.assertEqual(removed, 1)
            block = memory.read_memory_block()
            self.assertIn("keep me", block)
            self.assertNotIn("remove me", block)


if __name__ == "__main__":
    unittest.main()
