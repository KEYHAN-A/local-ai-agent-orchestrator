# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Critic Quorum aggregator and runner integration."""

from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from local_ai_agent_orchestrator.critic_quorum import (
    aggregate_critic_votes,
    critic_quorum_phase,
    pick_critic_models,
    quorum_size_for_risk,
)
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


class TestQuorumPicks(unittest.TestCase):
    def test_size_low_shrinks_to_one(self):
        self.assertEqual(quorum_size_for_risk("low", 3), 1)

    def test_size_med_uses_table_min_three(self):
        self.assertEqual(quorum_size_for_risk("med", 1), 3)

    def test_size_high_minimum_five(self):
        self.assertEqual(quorum_size_for_risk("high", 3), 5)

    def test_size_unknown_falls_back_to_base(self):
        self.assertEqual(quorum_size_for_risk(None, 3), 3)
        self.assertEqual(quorum_size_for_risk("xxx", 4), 4)

    def test_pick_models_pads_by_rotation(self):
        self.assertEqual(pick_critic_models(["a", "b"], 5), ["a", "b", "a", "b", "a"])
        self.assertEqual(pick_critic_models(["a"], 3), ["a", "a", "a"])
        self.assertEqual(pick_critic_models([], 3), [])
        self.assertEqual(pick_critic_models(["a", "b", "c"], 2), ["a", "b"])


class TestAggregateVotes(unittest.TestCase):
    def test_majority_approve(self):
        votes = [
            {"model": "A", "verdict": "approved", "findings": []},
            {"model": "B", "verdict": "approved", "findings": []},
            {"model": "C", "verdict": "rejected",
             "findings": [{"severity": "major", "file_path": "x.py", "message": "bad"}]},
        ]
        agg = aggregate_critic_votes(votes)
        self.assertEqual(agg["verdict"], "approved")
        self.assertEqual(agg["approve_count"], 2)
        self.assertEqual(agg["reject_count"], 1)
        self.assertAlmostEqual(agg["agreement_rate"], 0.667, places=2)
        self.assertEqual(len(agg["findings"]), 1)

    def test_tie_rejects(self):
        votes = [
            {"model": "A", "verdict": "approved", "findings": []},
            {"model": "B", "verdict": "rejected", "findings": []},
        ]
        self.assertEqual(aggregate_critic_votes(votes)["verdict"], "rejected")

    def test_findings_dedupe_by_file_and_message(self):
        votes = [
            {"model": "A", "verdict": "rejected", "findings": [
                {"severity": "major", "file_path": "x.py", "message": "Boom!"},
            ]},
            {"model": "B", "verdict": "rejected", "findings": [
                {"severity": "major", "file_path": "x.py", "message": "boom!"},
            ]},
            {"model": "C", "verdict": "rejected", "findings": [
                {"severity": "major", "file_path": "y.py", "message": "boom!"},
            ]},
        ]
        agg = aggregate_critic_votes(votes)
        # Two unique (file, message) pairs after lowercasing/normalising.
        self.assertEqual(len(agg["findings"]), 2)


class TestCriticPhase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        reset_settings_for_tests()
        init_settings(cwd=self.root, critic_quorum_enabled=True, critic_quorum_size=3)
        self.queue = TaskQueue(self.root / "state.db")

    def tearDown(self):
        self.queue.close()
        reset_settings_for_tests()
        self._tmp.cleanup()

    def _seed(self, risk="med"):
        plan_id = self.queue.register_plan("p.md", "x")
        self.queue.add_tasks(plan_id, [{
            "title": "T", "description": "d", "file_paths": [], "dependencies": [],
            "risk": risk,
        }])
        task = self.queue.get_plan_tasks(plan_id)[0]
        self.queue.mark_coded(task.id, "code body", code_signature="abc")
        return self.queue.get_plan_tasks(plan_id)[0]

    def test_quorum_persists_aggregate(self):
        task = self._seed(risk="med")

        def fake_vote(client, key, messages, cfg):
            verdict = "approved" if key.endswith("a") else "rejected"
            return {"model": key, "verdict": verdict, "findings": [], "summary": ""}

        mm = mock.Mock()
        mm.ensure_loaded.return_value = "fake"
        with mock.patch(
            "local_ai_agent_orchestrator.critic_quorum.pick_critic_models",
            return_value=["model_a", "model_b", "model_c"],
        ), mock.patch(
            "local_ai_agent_orchestrator.critic_quorum._vote_one_critic",
            side_effect=fake_vote,
        ):
            agg = critic_quorum_phase(mm, self.queue, task)

        self.assertEqual(agg["n"], 3)
        self.assertEqual(agg["approve_count"], 1)
        self.assertEqual(agg["reject_count"], 2)
        self.assertEqual(agg["verdict"], "rejected")
        loaded = self.queue.get_task_critic_votes(task.id)
        self.assertEqual(loaded["verdict"], "rejected")


if __name__ == "__main__":
    unittest.main()
