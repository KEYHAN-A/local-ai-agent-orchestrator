# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for interactive CLI helper flows."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
            with patch(
                "local_ai_agent_orchestrator.cli.ui.select_option", return_value="exit"
            ):
                choice = cli._home_menu(cwd, None)
            self.assertEqual(choice, "exit")

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

            answers = iter(["planner-old", "coder-old", "reviewer-new", "embed-old", "planner-old"])
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

    def _assert_root_guard(self, argv: list[str]):
        stderr = io.StringIO()
        with (
            patch("local_ai_agent_orchestrator.cli.Path.cwd", return_value=Path("/")),
            redirect_stderr(stderr),
        ):
            with self.assertRaises(SystemExit) as cm:
                cli.main(argv)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("filesystem root", stderr.getvalue())
        self.assertIn("project folder or subdirectory", stderr.getvalue())

    def test_main_blocks_no_command_at_root(self):
        self._assert_root_guard([])

    def test_main_blocks_run_at_root(self):
        self._assert_root_guard(["run"])

    def test_main_blocks_init_at_root(self):
        self._assert_root_guard(["init"])

    def test_home_menu_shows_warning_at_home_root(self):
        with (
            patch("local_ai_agent_orchestrator.cli.Path.home", return_value=Path("/Users/tester")),
            patch("local_ai_agent_orchestrator.cli._is_home_root", return_value=True),
            patch("local_ai_agent_orchestrator.cli.ui.print_warning") as warn,
            patch(
                "local_ai_agent_orchestrator.cli.ui.select_option", return_value="exit"
            ) as choose,
        ):
            choice = cli._home_menu(Path("/Users/tester"), None)
        self.assertEqual(choice, "exit")
        warn.assert_called_once()
        self.assertEqual(choose.call_args.args[2], "exit")

    def test_main_version_flag_short(self):
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                cli.main(["-v"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("lao 3.0.6", out.getvalue())

    def test_main_version_flag_long(self):
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                cli.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("lao 3.0.6", out.getvalue())


if __name__ == "__main__":
    unittest.main()
