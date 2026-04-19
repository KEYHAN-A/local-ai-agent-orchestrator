# SPDX-License-Identifier: GPL-3.0-or-later
"""Permission rule evaluation: modes, allow/deny globs, plan-mode."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator import permissions
from local_ai_agent_orchestrator.tools.base import get


YAML_BASE = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
permissions:
  mode: {mode}
  allow:
{allow}
  deny:
{deny}
"""


def _write_settings(td: Path, *, mode: str, allow: list[str], deny: list[str]) -> None:
    (td / ".lao").mkdir(parents=True, exist_ok=True)
    (td / "plans").mkdir(parents=True, exist_ok=True)
    cfg = td / "factory.yaml"
    cfg.write_text(
        YAML_BASE.format(
            mode=mode,
            allow="\n".join(f"    - \"{r}\"" for r in allow) or "    []",
            deny="\n".join(f"    - \"{r}\"" for r in deny) or "    []",
        ).strip(),
        encoding="utf-8",
    )
    init_settings(config_path=cfg, cwd=td)


class TestPermissionEvaluation(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()
        permissions.set_approval_hook(None)

    def test_auto_mode_allows_everything(self):
        with tempfile.TemporaryDirectory() as td:
            _write_settings(Path(td), mode="auto", allow=[], deny=[])
            tool = get("shell_exec")
            self.assertIsNotNone(tool)
            d = permissions.evaluate(tool, {"command": "echo hi"})
            self.assertTrue(d.granted)

    def test_deny_rule_blocks_dangerous_command(self):
        with tempfile.TemporaryDirectory() as td:
            _write_settings(Path(td), mode="auto", allow=[], deny=["Bash(rm -rf *)"])
            tool = get("shell_exec")
            d = permissions.evaluate(tool, {"command": "rm -rf /tmp/x"})
            self.assertFalse(d.granted)
            self.assertEqual(d.reason, "deny_rule")

    def test_plan_only_blocks_writes_but_allows_reads(self):
        with tempfile.TemporaryDirectory() as td:
            _write_settings(Path(td), mode="plan_only", allow=[], deny=[])
            read = permissions.evaluate(get("file_read"), {"path": "x"})
            write = permissions.evaluate(get("file_write"), {"path": "y", "content": "z"})
            self.assertTrue(read.granted)
            self.assertFalse(write.granted)

    def test_confirm_mode_uses_approval_hook(self):
        with tempfile.TemporaryDirectory() as td:
            _write_settings(Path(td), mode="confirm", allow=[], deny=[])
            seen = {}
            permissions.set_approval_hook(
                lambda name, args, prompt: seen.setdefault("called", True) and False
            )
            d = permissions.evaluate(get("file_write"), {"path": "x", "content": ""})
            self.assertFalse(d.granted)
            self.assertIn("called", seen)


if __name__ == "__main__":
    unittest.main()
