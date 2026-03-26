# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for analyzer registry and compiler-backed checks."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.analyzers import run_registered_analyzers


class TestAnalyzers(unittest.TestCase):
    def test_python_compile_analyzer_reports_syntax_errors(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.py"
            p.write_text("def x(:\n    pass\n", encoding="utf-8")
            rows = run_registered_analyzers(p, p.read_text(encoding="utf-8"))
            self.assertTrue(any(r.issue_class == "python_compile_error" for r in rows))


if __name__ == "__main__":
    unittest.main()

