# SPDX-License-Identifier: GPL-3.0-or-later
"""
Fixed-layout terminal dashboard for `lao run` (Rich Live).
Filters noisy log lines; surfaces agent progress, model swaps, and activity.
"""

from __future__ import annotations


import logging
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from local_ai_agent_orchestrator.branding import AUTHOR, DISPLAY as D, UPSTREAM_REPO

_active_dashboard: Optional["RunDashboard"] = None


def get_dashboard() -> Optional["RunDashboard"]:
    return _active_dashboard


def workspace_readme_body() -> str:
    return f"""# LAO workspace

This directory is a **Local AI Agent Orchestrator (LAO)** factory: a multi-agent coding pipeline that drives **local LLMs** (for example via [LM Studio](https://lmstudio.ai/))—no cloud API required for the runtime.

## Layout

| Path | Purpose |
|------|---------|
| `plans/` | Markdown plans (`MyFeature.md`). `plans/README.md` is never treated as a plan. |
| `{{plan-stem}}/` | Code for `plans/{{plan-stem}}.md` is written here, next to `plans/`. |
| `.lao/` | Internal state: SQLite DB, optional caches, fallback `.lao/_misc/`. |
| `factory.yaml` | Your config (model keys, paths, orchestration). Copy from `factory.example.yaml`. |

## Quick start

1. Copy `factory.example.yaml` to `factory.yaml` and set model IDs to match LM Studio.
2. Start LM Studio and enable the local server.
3. Run: `lao run` (or `lao --plan plans/YourPlan.md --single-run run`).

Upstream: {UPSTREAM_REPO}

Developer: {AUTHOR}
"""


def write_workspace_readme(root: Path) -> bool:
    dest = Path(root) / "README.md"
    if dest.exists():
        return False
    dest.write_text(workspace_readme_body(), encoding="utf-8")
    return True


class _DashboardLogHandler(logging.Handler):
    """Routes log records into the live dashboard with filtering."""

    def __init__(self, dash: "RunDashboard"):
        super().__init__(level=logging.INFO)
        self.dash = dash
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            raw = self.format(record)
            self.dash.process_log_line(raw, level=record.levelname)
        except Exception:
            self.handleError(record)


class RunDashboard:
    """
    Background Rich Live display + filtered activity log.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._console = Console(force_terminal=True)
        self._queue_ref: Optional[Callable[[], Any]] = None

        self._phase = "Starting"
        self._plan = "—"
        self._task = "—"
        self._attempt = ""
        self._model_line = "—"
        self._memory_line = ""
        self._idle_hint = ""
        self._log: deque[str] = deque(maxlen=72)
        self._handler: Optional[_DashboardLogHandler] = None

    def set_queue_getter(self, fn: Callable[[], Any]) -> None:
        self._queue_ref = fn

    def set_context(
        self,
        *,
        phase: Optional[str] = None,
        plan: Optional[str] = None,
        task: Optional[str] = None,
        attempt: Optional[str] = None,
        idle_hint: Optional[str] = None,
    ) -> None:
        with self._lock:
            if phase is not None:
                self._phase = phase
            if plan is not None:
                self._plan = plan
            if task is not None:
                self._task = task
            if attempt is not None:
                self._attempt = attempt
            if idle_hint is not None:
                self._idle_hint = idle_hint

    def append_activity(self, line: str) -> None:
        with self._lock:
            self._append_log_locked(line)

    def _append_log_locked(self, line: str) -> None:
        line = line.replace("\n", " ").strip()
        if len(line) > 200:
            line = line[:197] + "..."
        if line:
            self._log.append(line)

    def process_log_line(self, msg: str, level: str = "INFO") -> None:
        """Filter and route a single log message (thread-safe)."""
        m = msg.strip()
        if not m or "HTTP Request:" in m:
            return
        if set(m) <= {"=", "─", "-"}:
            return

        with self._lock:
            # Memory gate: show one live line, avoid log spam
            if "[MemoryGate]" in m:
                if "Waiting..." in m or m.startswith("[MemoryGate] Waiting for"):
                    self._memory_line = self._short_memory(m)
                    return
                if "Pages cleared" in m or "Timeout" in m or "Swap growing" in m:
                    self._append_log_locked(m)
                    self._memory_line = ""
                    return
                self._append_log_locked(m)
                return

            if "[ModelManager]" in m:
                self._model_line = m.replace("[ModelManager] ", "").strip()
                if (
                    "Loading" in m
                    or "Unloading" in m
                    or "Unload" in m
                    or "JIT" in m
                    or "Confirmed loaded" in m
                ):
                    self._append_log_locked(m)
                return

            if "Coding task #" in m:
                tm = re.search(
                    r"Coding task #(\d+):\s*(.+?)\s*\(attempt\s*(\d+)/(\d+)\)", m
                )
                if tm:
                    self._phase = "Coder"
                    self._task = f"#{tm.group(1)} {tm.group(2)}"
                    self._attempt = f"{tm.group(3)}/{tm.group(4)}"
                self._append_log_locked(m)
                return

            if m.startswith("[Architect]"):
                self._phase = "Architect"
                self._append_log_locked(m)
                return
            if m.startswith("[Coder]"):
                self._phase = "Coder"
                self._append_log_locked(m)
                return
            if m.startswith("[Reviewer]"):
                self._phase = "Reviewer"
                self._append_log_locked(m)
                return
            if m.startswith("[Tools]") or m.startswith("[State]"):
                self._append_log_locked(m)
                return

            if "New plan:" in m:
                self._append_log_locked(m)
                return
            if "Factory Status:" in m or "Total tokens:" in m:
                self._append_log_locked(m)
                return
            if "Shutdown requested" in m:
                self._append_log_locked(m)
                return

            if level == "WARNING" or level == "ERROR":
                self._append_log_locked(f"[{level}] {m}")
                return

            # Drop routine startup banners
            if "Local AI Agent Orchestrator" in m and "Models:" in m:
                return
            if "Per-plan project dirs:" in m or "Configured total RAM:" in m:
                return

    def _short_memory(self, m: str) -> str:
        if "available=" in m and "target=" in m:
            am = re.search(r"available=([\d.]+GB)", m)
            tm = re.search(r"target=([\d.]+GB)", m)
            if am and tm:
                return f"Memory settling  {am.group(1)} → target {tm.group(1)}"
        if "need +" in m:
            return m.replace("[MemoryGate] ", "")[:70]
        return "Memory settling…"

    def attach_logging(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.INFO)
        self._handler = _DashboardLogHandler(self)
        root.addHandler(self._handler)

        for name in ("httpx", "httpcore", "openai", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)

    def _render(self) -> Panel:
        with self._lock:
            phase = self._phase
            plan = self._plan
            task = self._task
            attempt = self._attempt
            model_line = self._model_line
            memory_line = self._memory_line
            idle = self._idle_hint
            log_lines = list(self._log)

        stats_txt = "—"
        if self._queue_ref is not None:
            try:
                q = self._queue_ref()
                stats = q.get_stats()
                if stats:
                    parts = [f"{k}: {v}" for k, v in sorted(stats.items())]
                    stats_txt = "  ".join(parts)
            except Exception:
                pass

        title = Text()
        title.append(" LAO ", style=f"bold {D['AI_SPARK_BRIGHT']}")
        title.append(" Local AI Agent Orchestrator ", style=D["TEXT"])
        title.append("│ ", style=D["TEXT_MUTED"])
        title.append(phase, style=f"bold {D['AI_SPARK']}")

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=D["TEXT_MUTED"], justify="right", width=12)
        grid.add_column(style=D["TEXT"])

        grid.add_row("Plan", plan)
        grid.add_row("Task", task + (f"  (attempt {attempt})" if attempt else ""))
        grid.add_row("Model", Text(model_line, style=D["AI_SPARK_BRIGHT"]))
        if memory_line:
            grid.add_row("Memory", Text(memory_line, style=D["WARNING_BRIGHT"]))
        if idle:
            grid.add_row("Watch", Text(idle, style=D["TEXT_MUTED"]))

        grid.add_row("Queue", stats_txt)

        log_text = Text()
        for line in log_lines[-24:]:
            if "APPROVED" in line or ("Created" in line and "micro-tasks" in line):
                log_text.append("▸ ", style=D["APPROVED_BRIGHT"])
                log_text.append(line + "\n", style=D["TEXT"])
            elif "REJECTED" in line or "FAILED" in line or "[ERROR]" in line:
                log_text.append("▸ ", style=D["WARNING_BRIGHT"])
                log_text.append(line + "\n", style=D["TEXT"])
            elif "[Reviewer]" in line or "[Architect]" in line:
                log_text.append("▸ ", style=D["AI_SPARK"])
                log_text.append(line + "\n", style=D["TEXT"])
            else:
                log_text.append("▸ ", style=D["TEXT_MUTED"])
                log_text.append(line + "\n", style=D["TEXT_MUTED"])

        body = Group(
            Align.left(title),
            Text(""),
            grid,
            Text(""),
            Text("Activity", style=f"bold {D['PANEL_ELEVATED']}"),
            Panel(
                log_text or Text("(waiting…)", style=D["TEXT_MUTED"]),
                border_style=D["PANEL_ELEVATED"],
                padding=(0, 1),
            ),
        )

        return Panel(
            body,
            title="[dim]Ctrl+C to stop after current step[/dim]",
            border_style=D["PANEL_ELEVATED"],
            style=f"on {D['BG']}",
            padding=(1, 2),
        )

    def _loop(self) -> None:
        try:
            with Live(
                self._render(),
                console=self._console,
                refresh_per_second=5,
                screen=True,
                transient=False,
            ) as live:
                while not self._stop.is_set():
                    live.update(self._render())
                    time.sleep(0.2)
        finally:
            pass

    def start(self) -> None:
        global _active_dashboard
        _active_dashboard = self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lao-dashboard", daemon=True)
        self._thread.start()
        time.sleep(0.05)

    def stop(self) -> None:
        global _active_dashboard
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        _active_dashboard = None

        root = logging.getLogger()
        if self._handler is not None:
            try:
                root.removeHandler(self._handler)
            except ValueError:
                pass
            self._handler = None

    def print_run_summary(self, queue: Any) -> None:
        """Plain summary after Live exits (scrollback-friendly)."""
        stats = queue.get_stats()
        tokens = queue.get_total_tokens()
        tbl = Table(title="LAO run finished", border_style=D["PANEL_ELEVATED"])
        tbl.add_column("Metric", style=D["TEXT_MUTED"])
        tbl.add_column("Value", style=D["TEXT"])
        for st, c in sorted(stats.items()):
            tbl.add_row(st, str(c))
        tbl.add_row(
            "Tokens",
            f"{tokens['prompt_tokens'] + tokens['completion_tokens']:,}",
        )
        self._console.print()
        self._console.print(Panel(tbl, border_style=D["AI_SPARK"]))


def apply_runner_context(
    *,
    phase: Optional[str] = None,
    plan: Optional[str] = None,
    task: Optional[str] = None,
    attempt: Optional[str] = None,
    idle_hint: Optional[str] = None,
) -> None:
    d = get_dashboard()
    if d is not None:
        d.set_context(
            phase=phase,
            plan=plan,
            task=task,
            attempt=attempt,
            idle_hint=idle_hint,
        )
