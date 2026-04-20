# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Contract Author phase and architect schema extension."""

from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from local_ai_agent_orchestrator.contract_author import (
    _extract_first_json_object,
    _infer_default_command,
    _materialise_tests,
    _normalise_commands,
    _parse_contract_payload,
    contract_author_phase,
)
from local_ai_agent_orchestrator.phases import _normalise_acceptance_block, _parse_architect_output
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


class TestArchitectSchema(unittest.TestCase):
    def test_accepts_acceptance_and_risk_fields(self):
        payload = json.dumps([
            {
                "title": "T1",
                "description": "do thing",
                "file_paths": ["src/x.py"],
                "dependencies": [],
                "acceptance": {
                    "acceptance_ids": ["AC-1"],
                    "tests": ["tests/acceptance/test_x.py"],
                },
                "risk": "high",
                "token_budget_estimate": 1200,
            }
        ])
        tasks = _parse_architect_output(payload)
        self.assertEqual(tasks[0]["risk"], "high")
        self.assertEqual(tasks[0]["acceptance"]["acceptance_ids"], ["AC-1"])
        self.assertEqual(tasks[0]["token_budget_estimate"], 1200)

    def test_legacy_payload_without_extras_still_parses(self):
        payload = json.dumps([
            {"title": "Legacy", "description": "ok", "file_paths": [], "dependencies": []}
        ])
        tasks = _parse_architect_output(payload)
        self.assertEqual(tasks[0]["title"], "Legacy")
        self.assertNotIn("risk", tasks[0])
        self.assertNotIn("acceptance", tasks[0])

    def test_normalise_acceptance_filters_unknown_keys(self):
        out = _normalise_acceptance_block({
            "acceptance_ids": ["AC-1", " AC-2 ", ""],
            "tests": "tests/single.py",
            "commands": ["pytest -q"],
            "allowed_major": -2,
            "garbage": "x",
        })
        self.assertEqual(out["acceptance_ids"], ["AC-1", "AC-2"])
        self.assertEqual(out["tests"], ["tests/single.py"])
        self.assertEqual(out["commands"], ["pytest -q"])
        self.assertEqual(out["allowed_major"], 0)
        self.assertNotIn("garbage", out)


class TestContractParser(unittest.TestCase):
    def test_extract_object_with_nested_strings(self):
        text = 'noise {"a": "}{", "b": [1,2]} trailing'
        self.assertEqual(
            json.loads(_extract_first_json_object(text)),
            {"a": "}{", "b": [1, 2]},
        )

    def test_parse_with_thinking_and_fences(self):
        raw = '<think>plan</think>```json\n{"tests": [], "commands": []}\n```'
        payload = _parse_contract_payload(raw)
        self.assertEqual(payload, {"tests": [], "commands": []})

    def test_parse_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            _parse_contract_payload("not json at all")

    def test_infer_default_command(self):
        self.assertEqual(
            _infer_default_command("tests/x.py", None), "pytest -q tests/x.py"
        )
        self.assertEqual(
            _infer_default_command("tests/x.test.ts", None),
            "npm test -- tests/x.test.ts",
        )
        self.assertEqual(_infer_default_command("tests/x.rs", None), "cargo test")

    def test_normalise_commands_falls_back_to_inference(self):
        cmds = _normalise_commands(None, ["tests/a.py", "tests/b.py"], None)
        self.assertEqual(cmds, ["pytest -q tests/a.py", "pytest -q tests/b.py"])


class TestMaterialiseTests(unittest.TestCase):
    def test_writes_files_under_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            written, skipped = _materialise_tests(ws, [
                {"path": "tests/acceptance/test_a.py", "content": "def test_x():\n    assert False\n"},
            ])
            self.assertEqual(written, ["tests/acceptance/test_a.py"])
            self.assertEqual(skipped, [])
            self.assertTrue((ws / "tests/acceptance/test_a.py").is_file())

    def test_refuses_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            written, skipped = _materialise_tests(ws, [
                {"path": "../escape.py", "content": "x"},
            ])
            self.assertEqual(written, [])
            self.assertEqual(skipped, ["../escape.py"])


class _FakeChoice:
    def __init__(self, content: str):
        self.message = types.SimpleNamespace(content=content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]
        self.usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=20)


class TestContractAuthorPhase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        reset_settings_for_tests()
        init_settings(cwd=self.root)
        self.queue = TaskQueue(self.root / "state.db")

    def tearDown(self) -> None:
        self.queue.close()
        reset_settings_for_tests()
        self._tmp.cleanup()

    def _make_task(self, ids):
        plan_id = self.queue.register_plan("p.md", "x")
        self.queue.add_tasks(plan_id, [{
            "title": "T1",
            "description": "implement thing",
            "file_paths": ["src/x.py"],
            "dependencies": [],
            "acceptance": {"acceptance_ids": ids},
        }])
        return plan_id, self.queue.get_plan_tasks(plan_id)[0]

    def test_writes_test_file_and_persists_acceptance(self):
        plan_id, task = self._make_task(["AC-1"])
        ws = self.queue.workspace_for_plan(plan_id)

        fake_payload = {
            "tests": [
                {"path": "tests/acceptance/test_t1.py", "content": "def test_ac1():\n    assert False\n"}
            ],
            "commands": ["pytest -q tests/acceptance/test_t1.py"],
            "acceptance_ids": ["AC-1"],
        }

        mm = mock.Mock()
        mm.ensure_loaded.return_value = "fake-model"
        with mock.patch(
            "local_ai_agent_orchestrator.contract_author._llm_call",
            return_value=_FakeResponse(json.dumps(fake_payload)),
        ):
            result = contract_author_phase(mm, self.queue, task, ws)

        self.assertIsNotNone(result)
        self.assertEqual(result["acceptance_ids"], ["AC-1"])
        self.assertEqual(result["commands"], ["pytest -q tests/acceptance/test_t1.py"])
        self.assertTrue((ws / "tests/acceptance/test_t1.py").is_file())
        loaded = self.queue.get_task_acceptance(task.id)
        self.assertEqual(loaded["commands"], ["pytest -q tests/acceptance/test_t1.py"])

    def test_skips_when_acceptance_already_executable(self):
        plan_id, task = self._make_task(["AC-1"])
        self.queue.set_task_acceptance(
            task.id, {"acceptance_ids": ["AC-1"], "commands": ["pytest -q t.py"]}
        )
        task = self.queue.get_plan_tasks(plan_id)[0]

        mm = mock.Mock()
        with mock.patch(
            "local_ai_agent_orchestrator.contract_author._llm_call"
        ) as call_mock:
            result = contract_author_phase(mm, self.queue, task, self.queue.workspace_for_plan(plan_id))

        self.assertEqual(result["commands"], ["pytest -q t.py"])
        call_mock.assert_not_called()

    def test_skips_when_no_acceptance_ids(self):
        plan_id = self.queue.register_plan("p.md", "x")
        self.queue.add_tasks(plan_id, [{
            "title": "T", "description": "d", "file_paths": [], "dependencies": []
        }])
        task = self.queue.get_plan_tasks(plan_id)[0]

        mm = mock.Mock()
        with mock.patch(
            "local_ai_agent_orchestrator.contract_author._llm_call"
        ) as call_mock:
            result = contract_author_phase(mm, self.queue, task, self.queue.workspace_for_plan(plan_id))

        self.assertIsNone(result)
        call_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
