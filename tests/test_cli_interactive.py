# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for interactive CLI helper flows."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from local_ai_agent_orchestrator import cli
from local_ai_agent_orchestrator.settings import reset_settings_for_tests


class _FakeMM:
    def health_check(self):
        return True

    def get_available_models(self):
        return ["planner-a", "coder-a", "reviewer-a", "embed-a"]

    def verify_models_exist(self):
        return []


class TestCliInteractiveHelpers(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_home_menu_quit_without_config(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("local_ai_agent_orchestrator.cli.ui.ask_choice", return_value="5"):
                choice = cli._home_menu(cwd, None)
            self.assertEqual(choice, 5)

    def test_configure_models_updates_file(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            cfg = cwd / "factory.yaml"
            cfg.write_text(
                yaml.dump(
                    {
                        "lm_studio_base_url": "http://127.0.0.1:1234",
                        "openai_api_key": "lm-studio",
                        "paths": {"plans": "./plans", "database": "./.lao/state.db"},
                        "models": {
                            "planner": {"key": "planner-old"},
                            "coder": {"key": "coder-old"},
                            "reviewer": {"key": "reviewer-old"},
                            "embedder": {"key": "embed-old"},
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (cwd / ".lao").mkdir(parents=True, exist_ok=True)
            (cwd / "plans").mkdir(parents=True, exist_ok=True)

            answers = iter(["planner-old", "coder-old", "reviewer-new", "embed-old"])
            with (
                patch("local_ai_agent_orchestrator.model_manager.ModelManager", _FakeMM),
                patch("local_ai_agent_orchestrator.cli.ui.ask_text", side_effect=lambda *_a, **_k: next(answers)),
                patch("local_ai_agent_orchestrator.cli._post_action_prompt", return_value=None),
            ):
                rc = cli._configure_models_interactive(cwd, cfg)
            self.assertEqual(rc, 0)

            data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
            self.assertEqual(data["models"]["reviewer"]["key"], "reviewer-new")

    def test_post_action_exit_noop(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            cfg = cwd / "factory.yaml"
            cfg.write_text(
                "lm_studio_base_url: http://127.0.0.1:1234\nopenai_api_key: lm-studio\n",
                encoding="utf-8",
            )
            with patch("local_ai_agent_orchestrator.cli.ui.ask_choice", return_value="exit"):
                cli._post_action_prompt(cwd, cfg, default="run")


if __name__ == "__main__":
    unittest.main()
