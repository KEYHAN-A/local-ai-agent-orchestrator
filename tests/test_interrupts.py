# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for interrupt responsiveness and shutdown propagation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from local_ai_agent_orchestrator import phases, runner
from local_ai_agent_orchestrator.interrupts import (
    interruptible_sleep,
    pilot_round_cancel_pending,
    register_interrupt,
    request_pilot_round_cancel,
    reset_interrupt_state,
    should_shutdown,
)
from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class _FakeCompletions:
    def create(self, **_kwargs):
        raise RuntimeError("transient failure")


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()


class TestInterrupts(unittest.TestCase):
    def tearDown(self):
        reset_interrupt_state()
        reset_settings_for_tests()

    def test_signal_tui_pilot_phase_first_sigint_soft_cancel(self):
        reset_interrupt_state()
        ui = MagicMock()
        ui.is_pilot_cancellable_phase.return_value = True
        with patch("local_ai_agent_orchestrator.unified_ui.get_unified_ui", return_value=ui):
            runner._signal_handler(None, None)
        self.assertFalse(should_shutdown())
        self.assertTrue(pilot_round_cancel_pending())

    def test_signal_tui_pilot_phase_second_sigint_registers_shutdown(self):
        reset_interrupt_state()
        request_pilot_round_cancel()
        ui = MagicMock()
        ui.is_pilot_cancellable_phase.return_value = True
        with patch("local_ai_agent_orchestrator.unified_ui.get_unified_ui", return_value=ui):
            runner._signal_handler(None, None)
        self.assertTrue(should_shutdown())

    def test_signal_handler_is_graceful_then_hard_abort(self):
        runner._signal_handler(None, None)
        self.assertTrue(should_shutdown())
        with self.assertRaises(KeyboardInterrupt):
            runner._signal_handler(None, None)

    def test_interruptible_sleep_returns_false_after_shutdown(self):
        register_interrupt()
        ok = interruptible_sleep(0.5, step_s=0.05)
        self.assertFalse(ok)

    def test_llm_call_aborts_during_retry_backoff(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root, llm_retry_attempts=3, llm_retry_backoff_base_s=1)
            with patch("local_ai_agent_orchestrator.phases.interruptible_sleep", return_value=False):
                with self.assertRaises(KeyboardInterrupt):
                    phases._llm_call(_FakeClient(), "model", [{"role": "user", "content": "x"}])

    def test_model_wait_until_loaded_aborts_when_shutdown_requested(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            mm = ModelManager(base_url="http://127.0.0.1:1234")
            register_interrupt()
            with self.assertRaises(KeyboardInterrupt):
                mm._wait_until_loaded("never-load")


if __name__ == "__main__":
    unittest.main()
