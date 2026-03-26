# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for quality report schema metadata and migration."""

import unittest

from local_ai_agent_orchestrator.report_schema import (
    QUALITY_REPORT_SCHEMA_VERSION,
    build_report_meta,
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


if __name__ == "__main__":
    unittest.main()

