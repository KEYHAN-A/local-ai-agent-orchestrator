"""Shared shutdown/interrupt state and interruptible sleep helpers."""

from __future__ import annotations

import time

_shutdown_requested = False
_interrupt_count = 0
_pilot_round_cancel_requested = False


def request_pilot_round_cancel() -> None:
    """Ask the pilot to abort the current LLM/tool round (no global shutdown)."""
    global _pilot_round_cancel_requested
    _pilot_round_cancel_requested = True


def pilot_round_cancel_pending() -> bool:
    return bool(_pilot_round_cancel_requested)


def clear_pilot_round_cancel() -> None:
    global _pilot_round_cancel_requested
    _pilot_round_cancel_requested = False


def register_interrupt() -> int:
    """Record a Ctrl+C/SIGTERM event and return total count."""
    global _interrupt_count, _shutdown_requested
    _interrupt_count += 1
    _shutdown_requested = True
    return _interrupt_count


def should_shutdown() -> bool:
    return bool(_shutdown_requested)


def reset_interrupt_state() -> None:
    global _shutdown_requested, _interrupt_count, _pilot_round_cancel_requested
    _shutdown_requested = False
    _interrupt_count = 0
    _pilot_round_cancel_requested = False


def interruptible_sleep(total_s: float, *, step_s: float = 0.2) -> bool:
    """
    Sleep in short slices so shutdown can be observed quickly.
    Returns False if interrupted by shutdown request, else True.
    """
    deadline = time.monotonic() + max(0.0, float(total_s))
    while True:
        if should_shutdown():
            return False
        now = time.monotonic()
        if now >= deadline:
            return True
        time.sleep(min(float(step_s), deadline - now))
