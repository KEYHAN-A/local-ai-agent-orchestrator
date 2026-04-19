# SPDX-License-Identifier: GPL-3.0-or-later
"""Mechanical verifier between coder and reviewer."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import MicroTask
from local_ai_agent_orchestrator.verifier import verify_task


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


def _task(file_paths: list[str]) -> MicroTask:
    return MicroTask(
        id=1,
        plan_id="p1",
        title="t",
        description="d",
        file_paths=file_paths,
    )


class TestVerifier(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_existing_python_file_passes(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            (Path(td) / "ok.py").write_text("x = 1\n", encoding="utf-8")
            report = verify_task(_task(["ok.py"]), Path(td), "Files written: ok.py")
        self.assertTrue(report.ok)
        self.assertIn("ok.py", report.files_checked)

    def test_missing_file_marks_critical(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            report = verify_task(_task(["does_not_exist.py"]), Path(td), "")
        self.assertFalse(report.ok)
        self.assertTrue(any(i.issue_class == "MissingFile" for i in report.issues))

    def test_python_syntax_error_is_critical(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            (Path(td) / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            report = verify_task(_task(["bad.py"]), Path(td), "")
        self.assertFalse(report.ok)
        self.assertTrue(any(i.issue_class == "SyntaxError" for i in report.issues))

    def test_path_outside_workspace_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            outside = Path(td).parent / "escape.py"
            try:
                outside.write_text("ok = 1\n", encoding="utf-8")
                report = verify_task(_task([str(outside)]), Path(td), "")
                self.assertFalse(report.ok)
                self.assertTrue(any(i.issue_class == "PathEscape" for i in report.issues))
            finally:
                if outside.exists():
                    outside.unlink()


if __name__ == "__main__":
    unittest.main()
