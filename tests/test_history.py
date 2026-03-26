# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for trend history persistence."""

import json
import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.history import append_history_entry


class TestHistory(unittest.TestCase):
    def test_append_history_entry_writes_and_trims(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = append_history_entry(root, "kpi_history.json", {"a": 1}, max_entries=2)
            self.assertTrue(out.exists())
            append_history_entry(root, "kpi_history.json", {"a": 2}, max_entries=2)
            append_history_entry(root, "kpi_history.json", {"a": 3}, max_entries=2)
            rows = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]["a"], 3)
            self.assertIn("captured_at", rows[-1])


if __name__ == "__main__":
    unittest.main()

