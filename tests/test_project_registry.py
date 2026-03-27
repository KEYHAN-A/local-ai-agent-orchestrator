# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the project registry module."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.project_registry import ProjectEntry, ProjectRegistry


class TestProjectEntry(unittest.TestCase):
    def test_touch_sets_timestamp(self):
        e = ProjectEntry(path="/tmp/x", name="x")
        self.assertEqual(e.last_used, "")
        e.touch()
        self.assertTrue(e.last_used)
        self.assertIn("T", e.last_used)


class TestProjectRegistryBasics(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.registry_path = self.td / "projects.json"
        self.reg = ProjectRegistry(registry_path=self.registry_path)

    def tearDown(self):
        self._td.cleanup()

    def test_empty_registry(self):
        self.assertEqual(self.reg.list_all(), [])

    def test_add_and_list(self):
        project_dir = self.td / "myproject"
        project_dir.mkdir()
        (project_dir / "factory.yaml").write_text("test: true")

        entry = self.reg.add(project_dir)
        self.assertEqual(entry.name, "myproject")
        self.assertEqual(entry.path, str(project_dir.resolve()))
        self.assertTrue(entry.has_config)

        entries = self.reg.list_all()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "myproject")

    def test_add_idempotent(self):
        project_dir = self.td / "proj"
        project_dir.mkdir()
        self.reg.add(project_dir)
        self.reg.add(project_dir)
        self.assertEqual(len(self.reg.list_all()), 1)

    def test_add_with_tags(self):
        project_dir = self.td / "tagged"
        project_dir.mkdir()
        entry = self.reg.add(project_dir, tags=["web", "python"])
        self.assertEqual(entry.tags, ["web", "python"])

    def test_remove_by_name(self):
        project_dir = self.td / "removeme"
        project_dir.mkdir()
        self.reg.add(project_dir)
        self.assertTrue(self.reg.remove("removeme"))
        self.assertEqual(len(self.reg.list_all()), 0)

    def test_remove_by_path(self):
        project_dir = self.td / "removepath"
        project_dir.mkdir()
        entry = self.reg.add(project_dir)
        self.assertTrue(self.reg.remove(entry.path))
        self.assertEqual(len(self.reg.list_all()), 0)

    def test_remove_nonexistent(self):
        self.assertFalse(self.reg.remove("nonexistent"))

    def test_get_by_name(self):
        project_dir = self.td / "findme"
        project_dir.mkdir()
        self.reg.add(project_dir)
        found = self.reg.get("findme")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "findme")

    def test_get_by_path(self):
        project_dir = self.td / "findpath"
        project_dir.mkdir()
        entry = self.reg.add(project_dir)
        found = self.reg.get(entry.path)
        self.assertIsNotNone(found)

    def test_get_not_found(self):
        self.assertIsNone(self.reg.get("nothing"))

    def test_persistence(self):
        project_dir = self.td / "persist"
        project_dir.mkdir()
        self.reg.add(project_dir)

        reg2 = ProjectRegistry(registry_path=self.registry_path)
        self.assertEqual(len(reg2.list_all()), 1)
        self.assertEqual(reg2.list_all()[0].name, "persist")


class TestProjectRegistryScan(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.registry_path = self.td / "projects.json"

    def tearDown(self):
        self._td.cleanup()

    def test_scan_finds_config_project(self):
        proj = self.td / "projects" / "alpha"
        proj.mkdir(parents=True)
        (proj / "factory.yaml").write_text("test: true")

        reg = ProjectRegistry(registry_path=self.registry_path)
        found = reg.scan(self.td / "projects")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "alpha")
        self.assertTrue(found[0].has_config)

    def test_scan_finds_plans_project(self):
        proj = self.td / "projects" / "beta"
        (proj / "plans").mkdir(parents=True)
        (proj / "plans" / "setup.md").write_text("# Setup")

        reg = ProjectRegistry(registry_path=self.registry_path)
        found = reg.scan(self.td / "projects")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "beta")

    def test_scan_finds_state_db_project(self):
        proj = self.td / "projects" / "gamma"
        (proj / ".lao").mkdir(parents=True)
        (proj / ".lao" / "state.db").write_text("")

        reg = ProjectRegistry(registry_path=self.registry_path)
        found = reg.scan(self.td / "projects")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "gamma")

    def test_scan_skips_non_lao_dirs(self):
        (self.td / "projects" / "empty").mkdir(parents=True)
        (self.td / "projects" / "also_empty").mkdir(parents=True)

        reg = ProjectRegistry(registry_path=self.registry_path)
        found = reg.scan(self.td / "projects")
        self.assertEqual(len(found), 0)

    def test_scan_multiple_projects(self):
        for name in ("one", "two", "three"):
            p = self.td / name
            p.mkdir()
            (p / "factory.yaml").write_text("x: 1")

        reg = ProjectRegistry(registry_path=self.registry_path)
        found = reg.scan(self.td)
        self.assertEqual(len(found), 3)
        names = {e.name for e in found}
        self.assertEqual(names, {"one", "two", "three"})

    def test_scan_respects_max_depth(self):
        deep = self.td / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "factory.yaml").write_text("deep: true")

        reg = ProjectRegistry(registry_path=self.registry_path)
        found = reg.scan(self.td, max_depth=2)
        self.assertEqual(len(found), 0)


class TestProjectRegistryRefresh(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.registry_path = self.td / "projects.json"

    def tearDown(self):
        self._td.cleanup()

    def test_refresh_updates_config_status(self):
        proj = self.td / "proj"
        proj.mkdir()
        reg = ProjectRegistry(registry_path=self.registry_path)
        entry = reg.add(proj)
        self.assertFalse(entry.has_config)

        (proj / "factory.yaml").write_text("x: 1")
        entry = reg.refresh(entry)
        self.assertTrue(entry.has_config)

    def test_refresh_counts_plans(self):
        proj = self.td / "proj"
        (proj / "plans").mkdir(parents=True)
        (proj / "plans" / "a.md").write_text("# A")
        (proj / "plans" / "b.md").write_text("# B")

        reg = ProjectRegistry(registry_path=self.registry_path)
        entry = reg.add(proj)
        self.assertEqual(entry.plans_count, 2)


class TestProjectRegistryNeedsAction(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.registry_path = self.td / "projects.json"

    def tearDown(self):
        self._td.cleanup()

    def test_needs_action_empty(self):
        reg = ProjectRegistry(registry_path=self.registry_path)
        self.assertEqual(reg.needs_action(), [])

    def test_needs_action_with_plans_no_config(self):
        proj = self.td / "proj"
        (proj / "plans").mkdir(parents=True)
        (proj / "plans" / "a.md").write_text("# A")

        reg = ProjectRegistry(registry_path=self.registry_path)
        reg.add(proj)
        urgent = reg.needs_action()
        self.assertEqual(len(urgent), 1)


if __name__ == "__main__":
    unittest.main()
