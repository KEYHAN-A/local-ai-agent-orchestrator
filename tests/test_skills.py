# SPDX-License-Identifier: GPL-3.0-or-later
"""Skills loader / registry / activation."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator import skills


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


class TestSkills(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()
        skills.deactivate()
        skills._REGISTRY.clear()

    def test_bundled_skills_load(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            loaded = skills.load_skills(force=True)
        names = set(loaded.keys())
        for required in {"verify", "stuck", "simplify", "write_tests"}:
            self.assertIn(required, names)

    def test_activation_changes_addendum(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            skills.load_skills(force=True)
            self.assertEqual(skills.active_addendum(), "")
            sk = skills.activate("verify")
            self.assertIsNotNone(sk)
            self.assertIn("verify", skills.active_addendum().lower())
            skills.deactivate()
            self.assertEqual(skills.active_addendum(), "")

    def test_user_skill_overrides_bundled(self):
        with tempfile.TemporaryDirectory() as td:
            _init(Path(td))
            user_dir = Path(td) / ".lao" / "skills"
            user_dir.mkdir(parents=True, exist_ok=True)
            (user_dir / "verify.md").write_text(
                "---\nname: verify\ndescription: custom\n---\nCUSTOM_BODY",
                encoding="utf-8",
            )
            skills.load_skills(force=True)
            sk = skills.get_skill("verify")
            self.assertIsNotNone(sk)
            self.assertEqual(sk.description, "custom")
            self.assertIn("CUSTOM_BODY", sk.body)


if __name__ == "__main__":
    unittest.main()
