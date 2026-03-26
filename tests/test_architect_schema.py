# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for strict architect output schema validation."""

import unittest

from local_ai_agent_orchestrator.phases import _parse_architect_output


class TestArchitectSchema(unittest.TestCase):
    def test_parse_accepts_valid_task(self):
        raw = """[
          {
            "title": "Implement API",
            "description": "Build endpoint",
            "file_paths": ["src/api.py"],
            "dependencies": [],
            "phase": "Phase 1",
            "deliverable_ids": ["REQ-1"]
          }
        ]"""
        tasks = _parse_architect_output(raw)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "Implement API")

    def test_parse_rejects_empty_or_wrong_types(self):
        raw = """[
          {
            "title": "",
            "description": "Build endpoint",
            "file_paths": "src/api.py",
            "dependencies": [],
            "deliverable_ids": [""]
          }
        ]"""
        with self.assertRaises(ValueError):
            _parse_architect_output(raw)


if __name__ == "__main__":
    unittest.main()

