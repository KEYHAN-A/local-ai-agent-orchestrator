# SPDX-License-Identifier: GPL-3.0-or-later
"""Conversation compaction service."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.services.compact import compact_messages
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests


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


class TestCompact(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_short_history_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}]
            out = compact_messages(msgs, keep_recent=4)
            self.assertEqual(out, msgs)

    def test_long_history_keeps_system_and_tail(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            msgs = [{"role": "system", "content": "SYS"}]
            for i in range(40):
                msgs.append({"role": "user", "content": f"u{i}"})
                msgs.append({"role": "assistant", "content": f"a{i}"})
            out = compact_messages(msgs, keep_recent=8, threshold=10)
        self.assertEqual(out[0]["content"], "SYS")
        self.assertLess(len(out), len(msgs))
        self.assertIn("Earlier turns summary", out[1]["content"])
        self.assertEqual(out[-1], msgs[-1])

    def test_summarizer_callback_is_invoked(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            msgs = [{"role": "system", "content": "S"}]
            for i in range(30):
                msgs.append({"role": "user", "content": f"u{i}"})
            calls = {"n": 0}

            def fake(_middle):
                calls["n"] += 1
                return "FAKE_SUMMARY"

            out = compact_messages(msgs, keep_recent=4, threshold=5, summarizer=fake)
        self.assertEqual(calls["n"], 1)
        self.assertIn("FAKE_SUMMARY", out[1]["content"])


if __name__ == "__main__":
    unittest.main()
