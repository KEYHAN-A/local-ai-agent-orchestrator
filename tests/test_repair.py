# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for deterministic repair payload construction."""

import unittest

from local_ai_agent_orchestrator.repair import build_repair_feedback
from local_ai_agent_orchestrator.repair import extract_feedback_signature, is_no_progress_repeat
from local_ai_agent_orchestrator.validators import Finding


class TestRepairBuilder(unittest.TestCase):
    def test_repair_feedback_is_deterministic_and_sorted(self):
        findings = [
            Finding(
                severity="minor",
                issue_class="style",
                message="nit",
                file_path="b.py",
                analyzer_id="x",
                analyzer_kind="heuristic",
                confidence=0.5,
            ),
            Finding(
                severity="critical",
                issue_class="runtime_error",
                message="boom",
                file_path="a.py",
                analyzer_id="x",
                analyzer_kind="runtime",
                confidence=0.9,
            ),
        ]
        out1 = build_repair_feedback(findings, contract_clause="Validation Contract")
        out2 = build_repair_feedback(list(reversed(findings)), contract_clause="Validation Contract")
        self.assertEqual(out1, out2)
        self.assertIn("[critical] a.py runtime_error: boom", out1)
        self.assertIsNotNone(extract_feedback_signature(out1))

    def test_no_progress_repeat_detection(self):
        f = [
            Finding(
                severity="major",
                issue_class="x",
                message="m",
                file_path="a.py",
                analyzer_id="x",
                analyzer_kind="heuristic",
                confidence=0.6,
            )
        ]
        prev = build_repair_feedback(f, contract_clause="Reviewer Contract")
        cur_same = build_repair_feedback(f, contract_clause="Reviewer Contract")
        cur_diff = build_repair_feedback(
            [Finding(severity="major", issue_class="y", message="n", file_path="a.py")],
            contract_clause="Reviewer Contract",
        )
        self.assertTrue(is_no_progress_repeat(prev, cur_same))
        self.assertFalse(is_no_progress_repeat(prev, cur_diff))


if __name__ == "__main__":
    unittest.main()

