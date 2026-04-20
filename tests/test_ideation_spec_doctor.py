# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Pilot ideation flow + Spec Doctor phase."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_ai_agent_orchestrator import ideation
from local_ai_agent_orchestrator.spec_doctor import (
    acceptance_ids_in,
    blocking_questions_in,
    spec_doctor_phase,
)
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests


class TestIdeation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_start_creates_status(self):
        s = ideation.start_ideation(self.ws, topic="Build a CLI todo app")
        self.assertEqual(s["topic"], "Build a CLI todo app")
        self.assertIsNone(s["locked_at"])
        self.assertEqual(s["turns"], 0)
        self.assertFalse(ideation.draft_path(self.ws).exists())

    def test_extract_draft_from_fenced_block(self):
        msg = (
            "Here is the current draft:\n"
            "```markdown\n"
            "# Problem\nUsers want X.\n"
            "```\n"
            "Next questions: ...\n"
        )
        body = ideation.extract_draft(msg)
        self.assertIn("Users want X.", body)
        self.assertNotIn("Next questions", body)

    def test_extract_draft_falls_back_to_full_text(self):
        msg = "# Problem\nUsers want Y."
        self.assertEqual(ideation.extract_draft(msg), "# Problem\nUsers want Y.")

    def test_apply_turn_updates_draft_and_history(self):
        ideation.start_ideation(self.ws, topic="t")
        ideation.apply_ideator_turn(
            self.ws,
            user_text="I want a CLI todo app",
            assistant_text="```markdown\n# Problem\nGreat\n```\nQ: who is the user?",
        )
        self.assertEqual(ideation.read_draft(self.ws), "# Problem\nGreat")
        history = ideation.read_history(self.ws)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(ideation.read_status(self.ws)["turns"], 1)

    def test_lock_requires_draft(self):
        ideation.start_ideation(self.ws, topic="t")
        with self.assertRaises(FileNotFoundError):
            ideation.lock_ideation(self.ws)

    def test_lock_then_unlock(self):
        ideation.start_ideation(self.ws, topic="t")
        ideation.apply_ideator_turn(self.ws, "go", "```markdown\n# Hi\n```")
        dest = ideation.lock_ideation(self.ws)
        self.assertTrue(dest.exists())
        self.assertTrue(ideation.is_locked(self.ws))
        ideation.unlock_ideation(self.ws)
        self.assertFalse(ideation.is_locked(self.ws))

    def test_blocking_questions_detected(self):
        ideation.start_ideation(self.ws, topic="t")
        ideation.apply_ideator_turn(
            self.ws, "go",
            "```markdown\n# Problem\nx\n## Open questions\n- BLOCKING: which DB?\n- NICE_TO_HAVE: theming?\n```",
        )
        qs = ideation.blocking_questions(self.ws)
        self.assertEqual(len(qs), 1)
        self.assertIn("BLOCKING", qs[0])


class TestSpecDoctorParsers(unittest.TestCase):
    def test_acceptance_ids_extracted(self):
        md = (
            "## Acceptance Criteria\n"
            "- AC-1: WHEN x THEN y\n"
            "- AC-2: WHEN a THEN b\n"
            "- AC-2: duplicate id\n"
            "- AC-10: WHEN long THEN fine\n"
        )
        self.assertEqual(acceptance_ids_in(md), ["AC-1", "AC-2", "AC-10"])

    def test_blocking_questions(self):
        md = (
            "## Open questions\n"
            "- BLOCKING: which auth provider?\n"
            "- NICE_TO_HAVE: dark mode\n"
        )
        out = blocking_questions_in(md)
        self.assertEqual(len(out), 1)
        self.assertIn("auth provider", out[0])


class TestSpecDoctorPhase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        reset_settings_for_tests()
        init_settings(cwd=self.ws)

    def tearDown(self):
        reset_settings_for_tests()
        self._tmp.cleanup()

    def test_requires_locked_ideation(self):
        mm = mock.Mock()
        with self.assertRaises(FileNotFoundError):
            spec_doctor_phase(mm, self.ws)

    def test_writes_spec_md_and_extracts_ids(self):
        ideation.start_ideation(self.ws, topic="todo")
        ideation.apply_ideator_turn(self.ws, "go", "```markdown\n# Problem\nx\n```")
        ideation.lock_ideation(self.ws)

        fake_response = mock.Mock()
        fake_response.choices = [mock.Mock()]
        fake_response.choices[0].message.content = (
            "# SPEC\n"
            "## Acceptance Criteria\n"
            "- AC-1: WHEN user runs `add` THEN a row exists\n"
            "- AC-2: WHEN user runs `list` THEN it prints rows\n"
            "## Open questions\n- BLOCKING: storage backend?\n"
        )
        fake_response.usage = mock.Mock(prompt_tokens=10, completion_tokens=20)

        mm = mock.Mock()
        mm.ensure_loaded.return_value = "fake-model"

        with mock.patch(
            "local_ai_agent_orchestrator.spec_doctor._get_client"
        ) as gc, mock.patch(
            "local_ai_agent_orchestrator.spec_doctor._llm_call",
            return_value=fake_response,
        ):
            gc.return_value = mock.Mock()
            report = spec_doctor_phase(mm, self.ws)

        spec_path = Path(report["spec_path"])
        self.assertTrue(spec_path.exists())
        self.assertEqual(report["acceptance_ids"], ["AC-1", "AC-2"])
        self.assertEqual(len(report["blocking_questions"]), 1)
        self.assertIn("AC-1", spec_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
