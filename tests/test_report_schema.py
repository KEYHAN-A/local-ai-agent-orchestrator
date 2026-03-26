# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for quality report schema metadata and migration."""

import unittest
import tempfile
from pathlib import Path

from local_ai_agent_orchestrator.report_schema import (
    QUALITY_REPORT_SCHEMA_VERSION,
    build_report_meta,
    check_quality_report_schema,
    load_and_migrate_quality_report,
    migrate_quality_report,
)


class TestReportSchema(unittest.TestCase):
    def test_build_meta_contains_version_fields(self):
        meta = build_report_meta()
        self.assertEqual(meta["schema_version"], QUALITY_REPORT_SCHEMA_VERSION)
        self.assertIn("min_compatible_version", meta)

    def test_migrate_adds_meta_when_missing(self):
        old = {"plan_id": "p1", "task_counts": {"total": 1}}
        migrated = migrate_quality_report(old)
        self.assertIn("report_meta", migrated)
        self.assertEqual(migrated["report_meta"]["schema_version"], QUALITY_REPORT_SCHEMA_VERSION)

    def test_file_level_migration_and_check_flow(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "quality_report.json"
            path.write_text('{"plan_id":"p1"}', encoding="utf-8")
            check_before = check_quality_report_schema(path)
            self.assertFalse(check_before["ok"])

            migrated = load_and_migrate_quality_report(path, write_back=True)
            self.assertEqual(migrated["report_meta"]["schema_version"], QUALITY_REPORT_SCHEMA_VERSION)

            check_after = check_quality_report_schema(path)
            self.assertTrue(check_after["ok"])


if __name__ == "__main__":
    unittest.main()

