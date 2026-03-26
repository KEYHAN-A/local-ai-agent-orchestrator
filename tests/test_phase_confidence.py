# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for confidence threshold policy resolution."""

import unittest

from local_ai_agent_orchestrator.phases import _finding_meets_block_confidence
from local_ai_agent_orchestrator.validators import Finding


class TestPhaseConfidencePolicy(unittest.TestCase):
    def test_default_threshold_applies(self):
        f = Finding(severity="major", issue_class="x", message="m", confidence=0.59)
        profile = {"block_min_confidence": 0.6}
        self.assertFalse(_finding_meets_block_confidence(f, profile))

    def test_kind_threshold_overrides_default(self):
        f = Finding(
            severity="major",
            issue_class="x",
            message="m",
            analyzer_kind="compiler",
            confidence=0.91,
        )
        profile = {
            "block_min_confidence": 0.6,
            "block_min_confidence_by_analyzer_kind": {"compiler": 0.95},
        }
        self.assertFalse(_finding_meets_block_confidence(f, profile))

    def test_id_threshold_overrides_kind(self):
        f = Finding(
            severity="major",
            issue_class="x",
            message="m",
            analyzer_id="python_py_compile",
            analyzer_kind="compiler",
            confidence=0.9,
        )
        profile = {
            "block_min_confidence": 0.6,
            "block_min_confidence_by_analyzer_kind": {"compiler": 0.95},
            "block_min_confidence_by_analyzer_id": {"python_py_compile": 0.85},
        }
        self.assertTrue(_finding_meets_block_confidence(f, profile))

    def test_new_analyzer_id_policy_can_block_when_default_would_not(self):
        f = Finding(
            severity="major",
            issue_class="json_parse_error",
            message="m",
            analyzer_id="json_structure",
            analyzer_kind="ast",
            confidence=0.7,
        )
        profile = {
            "block_min_confidence": 0.8,
            "block_min_confidence_by_analyzer_kind": {"ast": 0.9},
            "block_min_confidence_by_analyzer_id": {"json_structure": 0.65},
        }
        self.assertTrue(_finding_meets_block_confidence(f, profile))


if __name__ == "__main__":
    unittest.main()

