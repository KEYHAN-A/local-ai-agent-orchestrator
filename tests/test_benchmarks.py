# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for benchmark suite scaffolding."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.benchmarks import (
    run_benchmark_suite,
    write_benchmark_report,
)
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class TestBenchmarks(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_benchmark_suite_runs_and_writes_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            payload = run_benchmark_suite()
            self.assertEqual(payload["suite"], "core_reliability")
            self.assertEqual(payload["total"], 7)
            self.assertIn("pass_rate", payload)
            self.assertIn("gate", payload)
            out = write_benchmark_report(root, payload)
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()

