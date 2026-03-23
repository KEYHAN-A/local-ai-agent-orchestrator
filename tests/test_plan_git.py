# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for per-plan Git helpers (requires git on PATH)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator import plan_git
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue

GIT_OK = shutil.which("git") is not None


def _git_config_identity(repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "lao-test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "LAO Test"],
        check=True,
        capture_output=True,
    )


def _last_commit_subject(repo: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%s"],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


@unittest.skipUnless(GIT_OK, "git not on PATH")
class TestPlanGit(unittest.TestCase):
    def setUp(self):
        reset_settings_for_tests()
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()
        reset_settings_for_tests()

    def test_ensure_repo_and_gitignore(self):
        init_settings(config_path=None, cwd=self.tmp)
        ws = self.tmp / "W"
        ws.mkdir()
        plan_git.ensure_repo(ws)
        self.assertTrue((ws / ".git").exists())
        self.assertTrue((ws / ".gitignore").is_file())

    def test_commit_skips_when_nothing_staged(self):
        init_settings(config_path=None, cwd=self.tmp)
        ws = self.tmp / "W"
        plan_git.ensure_repo(ws)
        _git_config_identity(ws)
        plan_git.commit_all(ws, "lao(plan): bootstrap")
        self.assertFalse(plan_git.commit_all(ws, "lao(plan): empty"))

    def test_snapshot_and_architect_commits(self):
        init_settings(config_path=None, cwd=self.tmp)
        (self.tmp / ".lao").mkdir(parents=True, exist_ok=True)
        init_settings(
            config_path=None,
            cwd=self.tmp,
            db_path=self.tmp / ".lao" / "state.db",
        )
        q = TaskQueue()
        pid = q.register_plan("MyPlan.md", "unique plan content xyz")
        ws = q.workspace_for_plan(pid)
        plan_git.ensure_repo(ws)
        _git_config_identity(ws)

        plan_git.write_plan_snapshot(ws, "MyPlan.md", "# Title\n\nHello.")
        self.assertTrue(plan_git.commit_all(ws, "lao(plan): add plan snapshot for MyPlan"))
        self.assertIn("lao(plan):", _last_commit_subject(ws))

        q.add_tasks(
            pid,
            [
                {
                    "title": "Task one",
                    "description": "d",
                    "file_paths": [],
                    "dependencies": [],
                }
            ],
        )
        plan_git.commit_after_architect(ws, q, pid, "MyPlan", 1)
        self.assertIn("lao(architect):", _last_commit_subject(ws))

    def test_coder_reviewer_commits(self):
        init_settings(config_path=None, cwd=self.tmp)
        ws = self.tmp / "proj"
        plan_git.ensure_repo(ws)
        _git_config_identity(ws)
        (ws / "a.txt").write_text("v1\n", encoding="utf-8")
        plan_git.commit_after_coder(ws, "abc123deadbeef", 7, "Fix thing")
        sub = _last_commit_subject(ws)
        self.assertIn("lao(coder):", sub)
        self.assertIn("#7", sub)

        plan_git.commit_after_reviewer(ws, "abc123deadbeef", 7, "Fix thing", "approved")
        sub2 = _last_commit_subject(ws)
        self.assertIn("lao(reviewer):", sub2)
        self.assertTrue((ws / plan_git.REVIEW_LOG).exists())
