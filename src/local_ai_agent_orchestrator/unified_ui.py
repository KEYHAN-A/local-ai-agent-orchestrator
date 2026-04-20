# SPDX-License-Identifier: GPL-3.0-or-later
"""
LAO Unified Terminal UI — Professional Track (Option B).

Architecture layers:
  1. TerminalCapabilities  — probes env, honours LAO_UI_MODE / LAO_COLOR overrides
  2. RenderBus             — typed event queue; single writer, single renderer
  3. ViewComposer          — converts events to styled Rich renderables or plain text
  4. TerminalShell         — owns the prompt_toolkit session + Rich console; drives the loop
  5. LogBridge             — intercepts stdlib logging and feeds the RenderBus

Public surface (unchanged for callers):
  - UnifiedUI              — thin façade that wires all layers together
  - apply_runner_context() — status update helper used by runner.py
  - get_unified_ui()       — singleton accessor
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional, Union

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style as PTStyle
from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel

from local_ai_agent_orchestrator import __version__
from local_ai_agent_orchestrator.branding import DISPLAY as D

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Globals
# ─────────────────────────────────────────────────────────────────────────────

_active_ui: Optional["UnifiedUI"] = None
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][AB012]|\x1b=|\x1b>")


def get_unified_ui() -> Optional["UnifiedUI"]:
    return _active_ui


def pilot_cancellable_phase_active() -> bool:
    """True while pilot is inside an LLM request or processing that response's tools."""
    ui = get_unified_ui()
    if ui is None:
        return False
    return ui.is_pilot_cancellable_phase()


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — TerminalCapabilities
# ─────────────────────────────────────────────────────────────────────────────

class UIMode(Enum):
    AUTO = auto()
    RICH = auto()
    PLAIN = auto()


@dataclass(frozen=True)
class TerminalCapabilities:
    """
    Immutable snapshot of what the current terminal supports.

    Detection order (highest priority first):
      1. LAO_UI_MODE=rich|plain|auto  (explicit override)
      2. LAO_COLOR=1|0               (legacy colour override)
      3. NO_COLOR env var            (https://no-color.org)
      4. COLORTERM=truecolor|24bit   (explicit 24-bit support)
      5. TERM=xterm-256color etc.    (256-colour terminal)
      6. SSH_TTY present             (remote session — keep rich but note it)
      7. isatty() probe              (non-interactive → plain)
    """

    interactive: bool
    supports_color: bool
    supports_unicode: bool
    supports_alt_screen: bool
    color_depth: int          # 0=none, 8=basic, 256=256col, 16777216=truecolor
    width: int
    mode: UIMode
    via_ssh: bool

    @classmethod
    def probe(cls) -> "TerminalCapabilities":
        # --- mode override ---
        raw_mode = os.getenv("LAO_UI_MODE", "").strip().lower()
        if raw_mode == "rich":
            forced_mode = UIMode.RICH
        elif raw_mode == "plain":
            forced_mode = UIMode.PLAIN
        else:
            forced_mode = UIMode.AUTO

        # --- legacy colour override ---
        raw_color = os.getenv("LAO_COLOR", "").strip().lower()
        if raw_color in {"1", "true", "yes", "on"}:
            color_forced = True
        elif raw_color in {"0", "false", "no", "off"}:
            color_forced = False
        else:
            color_forced = None

        interactive = sys.stdout.isatty() and sys.stdin.isatty()
        via_ssh = bool(os.getenv("SSH_TTY"))

        # --- colour depth detection ---
        no_color = bool(os.getenv("NO_COLOR"))
        colorterm = os.getenv("COLORTERM", "").lower()
        term = os.getenv("TERM", "").lower()

        if color_forced is False or no_color:
            color_depth = 0
        elif color_forced is True:
            color_depth = 16777216
        elif colorterm in ("truecolor", "24bit"):
            color_depth = 16777216
        elif "256color" in term or colorterm == "256color":
            color_depth = 256
        elif interactive:
            color_depth = 8
        else:
            color_depth = 0

        supports_color = color_depth > 0

        # --- unicode ---
        enc = getattr(sys.stdout, "encoding", "") or ""
        supports_unicode = enc.lower().replace("-", "") in ("utf8", "utf-8")

        # --- alt-screen (only useful in truly interactive sessions) ---
        supports_alt_screen = interactive and not via_ssh

        # --- terminal width ---
        width = shutil.get_terminal_size((100, 24)).columns
        width = max(60, min(220, width))

        # --- resolve final mode ---
        if forced_mode == UIMode.RICH:
            mode = UIMode.RICH
        elif forced_mode == UIMode.PLAIN:
            mode = UIMode.PLAIN
        else:
            mode = UIMode.RICH if (interactive and supports_color) else UIMode.PLAIN

        return cls(
            interactive=interactive,
            supports_color=supports_color,
            supports_unicode=supports_unicode,
            supports_alt_screen=supports_alt_screen,
            color_depth=color_depth,
            width=width,
            mode=mode,
            via_ssh=via_ssh,
        )

    @property
    def rich(self) -> bool:
        return self.mode == UIMode.RICH


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — RenderBus event model
# ─────────────────────────────────────────────────────────────────────────────

class EventKind(Enum):
    ACTIVITY      = "activity"
    USER_MSG      = "user_msg"
    ASSISTANT_MSG = "assistant_msg"
    TOOL_CALL     = "tool_call"
    TOOL_RESULT   = "tool_result"
    USAGE         = "usage"
    THINKING      = "thinking"
    TRANSITION    = "transition"
    REPORT        = "report"
    INFO          = "info"
    ERROR         = "error"
    BANNER        = "banner"
    STATUS_UPDATE = "status_update"


@dataclass
class RenderEvent:
    kind: EventKind
    payload: dict[str, Any]
    ts: float = field(default_factory=time.monotonic)


class RenderBus:
    """
    Thread-safe event queue.  Producers call put(); the TerminalShell drains it.

    In the current synchronous model the shell drains immediately on each put()
    (no background thread needed).  The queue acts as a serialisation point so
    that concurrent log handlers never interleave partial writes.
    """

    def __init__(self) -> None:
        self._q: queue.SimpleQueue[RenderEvent] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._consumer: Optional[Callable[[RenderEvent], None]] = None

    def set_consumer(self, fn: Callable[[RenderEvent], None]) -> None:
        with self._lock:
            self._consumer = fn

    def put(self, event: RenderEvent) -> None:
        with self._lock:
            consumer = self._consumer
        if consumer is not None:
            consumer(event)
        else:
            self._q.put(event)

    def drain_pending(self, fn: Callable[[RenderEvent], None]) -> None:
        """Flush any events queued before a consumer was attached."""
        while True:
            try:
                fn(self._q.get_nowait())
            except queue.Empty:
                break


# ─────────────────────────────────────────────────────────────────────────────
# Sanitisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def sanitize_for_terminal(text: str, *, width: int = 120) -> str:
    """
    Strip ANSI escapes and hard-wrap at *width* so no line overflows the
    terminal.  Safe to call on any string before display.
    """
    cleaned = _ANSI_RE.sub("", text or "")
    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        while len(raw_line) > width:
            lines.append(raw_line[:width])
            raw_line = raw_line[width:]
        lines.append(raw_line)
    return "\n".join(lines)


def _trunc(s: str, n: int) -> str:
    s = sanitize_for_terminal(s, width=n + 10)
    return s if len(s) <= n else s[: n - 1] + "…"


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _model_swap_mini_bar(si: int, width: int = 8) -> str:
    """Indeterminate ASCII bar for plain-toolbar model swap feedback."""
    if width < 3:
        width = 3
    span = 2
    pos = (si * 2) % (width + span)
    parts: list[str] = []
    for i in range(width):
        parts.append("#" if pos - span < i <= pos else "-")
    return "[" + "".join(parts) + "]"


def _model_swap_mini_bar_html(si: int, width: int = 8) -> str:
    """Knight-rider style bar for prompt_toolkit HTML toolbar."""
    if width < 3:
        width = 3
    span = 2
    pos = (si * 2) % (width + span)
    chunks: list[str] = []
    for i in range(width):
        filled = pos - span < i <= pos
        ch = "█" if filled else "░"
        style = "ansicyan" if filled else "ansibrightblack"
        chunks.append(f"<{style}>{_esc_html(ch)}</{style}>")
    return (
        "<ansibrightblack>[</ansibrightblack>"
        + "".join(chunks)
        + "<ansibrightblack>]</ansibrightblack>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 — ViewComposer
# ─────────────────────────────────────────────────────────────────────────────

class ViewComposer:
    """
    Converts RenderEvents into Rich renderables (rich mode) or plain strings
    (plain mode).  Knows nothing about I/O — just returns objects.
    """

    # Separator characters chosen for readability at 80+ col widths
    _SEP_HEAVY = "━"
    _SEP_LIGHT = "─"
    _SEP_DOT   = "·"

    def __init__(self, caps: TerminalCapabilities) -> None:
        self._caps = caps

    # ── public entry point ──────────────────────────────────────────────────

    def compose(self, event: RenderEvent) -> list[Any]:
        """Return a list of Rich renderables or plain strings."""
        k = event.kind
        p = event.payload
        if k == EventKind.BANNER:
            return self._banner()
        if k == EventKind.USER_MSG:
            return self._user_msg(p["content"])
        if k == EventKind.ASSISTANT_MSG:
            return self._assistant_msg(p["content"])
        if k == EventKind.TOOL_CALL:
            return self._tool_call(p["name"], p.get("args", {}))
        if k == EventKind.TOOL_RESULT:
            return self._tool_result(p["name"], p["result"])
        if k == EventKind.USAGE:
            return self._usage(p["prompt"], p["completion"])
        if k == EventKind.THINKING:
            return self._thinking(p["hint"])
        if k == EventKind.TRANSITION:
            return self._transition(p["from_mode"], p["to_mode"])
        if k == EventKind.REPORT:
            return self._report(p["title"], p["rows"])
        if k == EventKind.INFO:
            return self._info(p["msg"])
        if k == EventKind.ERROR:
            return self._error(p["msg"], p.get("suggestion", ""))
        if k == EventKind.ACTIVITY:
            return self._activity(p["msg"], p.get("level", "INFO"))
        return []

    # ── renderable builders ─────────────────────────────────────────────────

    def thinking_strip(self, lines: list[str]) -> list[Any]:
        """Compact rolling panel for pilot / LLM thinking hints (compact mode)."""
        if not lines:
            return []
        body = Text()
        for ln in lines:
            body.append(ln + "\n", style=D["TEXT_MUTED"])
        panel = Panel(
            body,
            title="thinking",
            border_style=D["PANEL_ELEVATED"],
            width=max(44, min(self._caps.width - 2, 96)),
            padding=(0, 1),
        )
        return [Text(""), panel, Text("")]

    def _banner(self) -> list[Any]:
        from local_ai_agent_orchestrator.settings import get_settings

        w = max(60, self._caps.width)
        s = get_settings()
        art = [
            " ██╗      █████╗  ██████╗ ",
            " ██║     ██╔══██╗██╔═══██╗",
            " ██║     ███████║██║   ██║",
            " ██║     ██╔══██║██║   ██║",
            " ███████╗██║  ██║╚██████╔╝",
            " ╚══════╝╚═╝  ╚═╝ ╚═════╝ ",
        ]
        if self._caps.rich:
            right_w = min(40, max(28, w // 3))
            left_lines = Text()
            for ln in art:
                left_lines.append(ln + "\n", style=f"bold {D['AI_SPARK_BRIGHT']}")
            left_lines.append("\n")
            left_lines.append(f"LAO v{__version__}\n", style=f"bold {D['TEXT']}")
            left_lines.append(
                "Local AI coding orchestrator — plan · run · inspect · chat\n\n",
                style=D["TEXT_MUTED"],
            )
            left_lines.append(
                "/help  /status  /resume  /clear  /exit\n"
                "Enter send  ·  Alt+Enter newline  ·  Ctrl+O trace\n",
                style=D["TEXT_MUTED"],
            )
            cfg_line = str(s.config_dir)
            if len(cfg_line) > right_w + 8:
                cfg_line = "…" + cfg_line[-(right_w + 4) :]
            right_body = Text()
            right_body.append("LAO workspace\n", style=f"bold {D['AI_SPARK_BRIGHT']}")
            right_body.append("Config\n", style=D["TEXT_MUTED"])
            right_body.append(cfg_line + "\n\n", style=D["TEXT"])
            right_body.append("You are inside a LAO session.", style=D["TEXT_MUTED"])
            right_panel = Panel(
                right_body,
                title="status",
                border_style=D["AI_SPARK"],
                width=right_w,
                padding=(0, 1),
            )
            row = Columns(
                [Align.left(Group(left_lines), vertical="top"), Align.right(right_panel)],
                expand=True,
                equal=False,
            )
            return [
                Text(""),
                Rule(style=D["AI_SPARK"]),
                row,
                Rule(style=D["PANEL_ELEVATED"]),
                Text(""),
            ]

        bar = self._SEP_HEAVY * min(w, 72)
        lines_out: list[Any] = ["", bar]
        for ln in art:
            lines_out.append(ln.rstrip())
        lines_out.append(f"LAO v{__version__}")
        lines_out.append("Local AI coding orchestrator — plan · run · inspect · chat")
        lines_out.append(str(s.config_dir))
        lines_out.append(bar)
        lines_out.append("  /help  /status  /resume  /clear  /exit")
        lines_out.append("  Enter send | Alt+Enter newline | Ctrl+O trace")
        lines_out.append(bar)
        lines_out.append("")
        return lines_out

    def _user_msg(self, content: str) -> list[Any]:
        content = sanitize_for_terminal(content, width=self._caps.width - 8)
        if self._caps.rich:
            t = Text()
            t.append("  You  ", style=f"bold {D['APPROVED_BRIGHT']}")
            t.append(content, style=D["TEXT"])
            return [Text(""), t, Text("")]
        return ["", f"  You  {content}", ""]

    def _assistant_msg(self, content: str) -> list[Any]:
        content = sanitize_for_terminal(content, width=self._caps.width - 10)
        if self._caps.rich:
            t = Text()
            t.append("  Pilot  ", style=f"bold {D['AI_SPARK_BRIGHT']}")
            t.append(content, style=D["TEXT"])
            return [Text(""), t, Text("")]
        return ["", f"  Pilot  {content}", ""]

    def _tool_call(self, name: str, args: dict) -> list[Any]:
        name = sanitize_for_terminal(name)
        args_str = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        line = f"  ▶ {name}({args_str})" if args_str else f"  ▶ {name}()"
        if self._caps.rich:
            t = Text(sanitize_for_terminal(line, width=self._caps.width - 4), style=D["TEXT_MUTED"])
            return [t]
        return [sanitize_for_terminal(line)]

    def _tool_result(self, name: str, result: str) -> list[Any]:
        name = sanitize_for_terminal(name)
        first = sanitize_for_terminal(result).strip().split("\n")[0][:100]
        ok = not first.startswith("ERROR")
        icon = "  ✓" if ok else "  ✗"
        line = f"{icon} {name}: {first}"
        if self._caps.rich:
            style = D["APPROVED_BRIGHT"] if ok else D["WARNING_BRIGHT"]
            return [Text(sanitize_for_terminal(line, width=self._caps.width - 4), style=style)]
        return [sanitize_for_terminal(line)]

    def _usage(self, prompt: int, completion: int) -> list[Any]:
        total = prompt + completion
        line = f"  tokens {total:,}  (↑{prompt:,} prompt  ↓{completion:,} completion)"
        if self._caps.rich:
            return [Text(line, style=D["TEXT_MUTED"])]
        return [line]

    def _thinking(self, hint: str) -> list[Any]:
        hint = sanitize_for_terminal(hint, width=self._caps.width - 8)
        line = f"  … {hint}"
        if self._caps.rich:
            return [Text(line, style=D["TEXT_MUTED"])]
        return [line]

    def _transition(self, from_mode: str, to_mode: str) -> list[Any]:
        from_mode = sanitize_for_terminal(from_mode)
        to_mode = sanitize_for_terminal(to_mode)
        label = f"  {from_mode} → {to_mode}"
        if self._caps.rich:
            return [Text(""), Rule(label, style=D["PANEL_ELEVATED"]), Text("")]
        w = min(self._caps.width, 72)
        bar = self._SEP_LIGHT * w
        return ["", bar, f"  {from_mode} -> {to_mode}", bar, ""]

    def _report(self, title: str, rows: list[tuple[str, str]]) -> list[Any]:
        title = sanitize_for_terminal(title)
        rows = [(sanitize_for_terminal(k), sanitize_for_terminal(v)) for k, v in rows]
        if self._caps.rich:
            tbl = Table(
                title=title,
                border_style=D["PANEL_ELEVATED"],
                show_edge=True,
                show_header=False,
                padding=(0, 1),
            )
            tbl.add_column("", style=D["TEXT_MUTED"], no_wrap=True, min_width=14)
            tbl.add_column("", style=D["TEXT"])
            for k, v in rows:
                tbl.add_row(k, v)
            return [Text(""), tbl, Text("")]
        lines: list[Any] = ["", f"── {title} ──"]
        for k, v in rows:
            lines.append(f"  {k:<18} {v}")
        lines.append("")
        return lines

    def _info(self, msg: str) -> list[Any]:
        msg = sanitize_for_terminal(msg, width=self._caps.width - 4)
        if self._caps.rich:
            return [Text(f"  {msg}", style=D["TEXT_MUTED"])]
        return [f"  {msg}"]

    def _error(self, msg: str, suggestion: str) -> list[Any]:
        msg = sanitize_for_terminal(msg, width=self._caps.width - 4)
        suggestion = sanitize_for_terminal(suggestion, width=self._caps.width - 6)
        if self._caps.rich:
            items: list[Any] = [Text(""), Text(f"  ✗ {msg}", style=D["WARNING_BRIGHT"])]
            if suggestion:
                items.append(Text(f"    → {suggestion}", style=D["TEXT_MUTED"]))
            items.append(Text(""))
            return items
        lines: list[Any] = ["", f"  ERROR: {msg}"]
        if suggestion:
            lines.append(f"    -> {suggestion}")
        lines.append("")
        return lines

    def _activity(self, msg: str, level: str) -> list[Any]:
        msg = sanitize_for_terminal(msg, width=self._caps.width - 14)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if not self._caps.rich:
            prefix = {"WARNING": "!", "ERROR": "X"}.get(level, ">")
            return [f" {ts} {prefix} {msg}"]

        style = D["TEXT_MUTED"]
        prefix = "  "
        if level in ("WARNING", "ERROR"):
            style = D["WARNING_BRIGHT"]
            prefix = "! " if level == "WARNING" else "✗ "
        elif any(k in msg for k in ("APPROVED", "Created", "micro-tasks")):
            style = D["APPROVED_BRIGHT"]
            prefix = "+ "
        elif any(k in msg for k in ("REJECTED", "FAILED")):
            style = D["WARNING_BRIGHT"]
            prefix = "- "
        elif any(k in msg for k in ("[Architect]", "[Reviewer]", "[Pilot]")):
            style = D["AI_SPARK_BRIGHT"]
            prefix = "▸ "
        elif "[Coder]" in msg:
            style = D["TEXT"]
            prefix = "  "

        t = Text()
        t.append(f" {ts} ", style="dim")
        t.append(prefix, style=style)
        t.append(msg, style=style)
        return [t]


# ─────────────────────────────────────────────────────────────────────────────
# Slash-command completer
# ─────────────────────────────────────────────────────────────────────────────

_SLASH_COMMANDS: dict[str, str] = {
    "/help":   "Show available commands",
    "/status": "Show pipeline status",
    "/resume": "Return to autopilot pipeline",
    "/clear":  "Clear chat history",
    "/exit":   "Exit LAO",
    "/quit":   "Exit LAO",
}


class SlashCommandCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return
        for cmd, desc in _SLASH_COMMANDS.items():
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display_meta=desc)


# ─────────────────────────────────────────────────────────────────────────────
# Layer 4 — TerminalShell
# ─────────────────────────────────────────────────────────────────────────────

class TerminalShell:
    """
    Owns the Rich Console and prompt_toolkit PromptSession.
    Consumes RenderEvents from the bus and writes them to the terminal.

    Key design decision: Rich's Console is configured with ``no_color=True``
    inside ``patch_stdout()``.  Rich ANSI escape sequences are mangled by
    prompt_toolkit's StdoutProxy on many terminals (the ESC byte appears as
    ``?``).  We avoid this entirely:

    - **Banner**: printed BEFORE ``patch_stdout()`` via a direct Console that
      writes to the real terminal — full colour works.
    - **Body**: printed INSIDE ``patch_stdout()`` via a ``no_color=True``
      Console — structural formatting (tables, rules, Unicode icons) renders
      cleanly, with zero risk of ANSI leakage.
    - **Toolbar / prompt**: styled by prompt_toolkit's native HTML — always
      correct.
    """

    def __init__(
        self,
        caps: TerminalCapabilities,
        composer: ViewComposer,
        bus: RenderBus,
        *,
        history_path: Optional[Path] = None,
        skip_initial_banner: bool = False,
    ) -> None:
        self._caps = caps
        self._composer = composer
        self._bus = bus
        self._history_path = history_path

        # Body console: always no_color to prevent ANSI leakage through
        # prompt_toolkit's patch_stdout proxy.
        self._console = Console(
            force_terminal=False,
            no_color=True,
            highlight=False,
            markup=False,
            emoji=False,
        )

        self._pt_session: Optional[PromptSession] = None
        self._patch_ctx: Optional[Any] = None

        self._last_ctrl_c: float = 0.0
        self._skip_initial_banner = bool(skip_initial_banner)

        # Model swap indicator (toolbar corner)
        self._model_swap_label = ""
        self._model_swap_spin = 0

        # Status bar state (written from any thread, read by toolbar callback)
        self._lock = threading.Lock()
        self._phase = "Starting"
        self._model_line = ""
        self._task = ""
        self._memory_line = ""
        self._activity_expanded = False

        # Activity ring buffers
        self._activity_compact: deque[str] = deque(maxlen=6)
        self._activity_full: deque[str] = deque(maxlen=80)
        self._thinking_ring: deque[str] = deque(maxlen=5)

        self._session_start = time.monotonic()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        # Print banner BEFORE patch_stdout — writes directly to the real
        # terminal so Rich ANSI codes render correctly.
        if self._skip_initial_banner:
            self._print_minimal_session_marker()
        else:
            self._print_banner_direct()

        # NOW start patch_stdout.  All subsequent Console output uses the
        # no_color body console, so no ANSI codes pass through the proxy.
        self._patch_ctx = patch_stdout()
        self._patch_ctx.__enter__()
        self._bus.set_consumer(self._handle_event)
        self._bus.drain_pending(self._handle_event)

    def _print_banner_direct(self) -> None:
        """Render the banner with a direct Console (full colour on capable terminals)."""
        if self._caps.rich:
            direct = Console(
                force_terminal=True,
                highlight=False,
                markup=False,
                emoji=False,
            )
        else:
            direct = Console(
                force_terminal=False,
                no_color=True,
                highlight=False,
                markup=False,
                emoji=False,
            )
        banner_event = RenderEvent(EventKind.BANNER, {})
        for renderable in self._composer.compose(banner_event):
            direct.print(renderable)


    def _print_minimal_session_marker(self) -> None:
        """Single-line entry marker when the CLI already showed branding."""
        line = f"LAO v{__version__} — session started  (/help for commands)"
        if self._caps.rich:
            direct = Console(
                force_terminal=True,
                highlight=False,
                markup=False,
                emoji=False,
            )
            direct.print(Text(line, style=D["TEXT_MUTED"]))
        else:
            print(line)

    def stop(self) -> None:
        self._bus.set_consumer(None)
        if self._patch_ctx is not None:
            try:
                self._patch_ctx.__exit__(None, None, None)
            except Exception:
                pass
            self._patch_ctx = None

    # ── event handling ───────────────────────────────────────────────────────

    def _handle_event(self, event: RenderEvent) -> None:
        if event.kind == EventKind.BANNER:
            return  # Already rendered by _print_banner_direct

        if not self._activity_expanded and event.kind in (
            EventKind.TOOL_CALL,
            EventKind.TOOL_RESULT,
        ):
            if event.kind == EventKind.TOOL_CALL:
                n = sanitize_for_terminal(event.payload.get("name", ""))
                self._activity_compact.append(f"▶ {n}")
                self._activity_full.append(f"▶ {n}")
            else:
                n = sanitize_for_terminal(event.payload.get("name", ""))
                first = sanitize_for_terminal(event.payload.get("result", "")).strip().split("\n")[0][:120]
                self._activity_compact.append(f"{n}: {first}")
                self._activity_full.append(f"{n}: {first}")
            return

        if event.kind == EventKind.THINKING and not self._activity_expanded:
            hint = sanitize_for_terminal(event.payload.get("hint", ""), width=self._caps.width - 8)
            if hint:
                self._thinking_ring.append(hint)
                self._activity_compact.append(f"… {hint}")
                self._activity_full.append(f"… {hint}")
            for r in self._composer.thinking_strip(list(self._thinking_ring)):
                self._console.print(r)
            return

        renderables = self._composer.compose(event)
        for r in renderables:
            self._console.print(r)

        if event.kind == EventKind.ACTIVITY:
            msg = sanitize_for_terminal(event.payload.get("msg", "")).strip().replace("\n", " ")
            if msg:
                self._activity_compact.append(msg)
                self._activity_full.append(msg)

    # ── toolbar ──────────────────────────────────────────────────────────────

    def set_model_swap_status(self, label: str) -> None:
        with self._lock:
            self._model_swap_label = (label or "").strip()
            self._model_swap_spin = 0
        self._invalidate_prompt_app()

    def bump_model_swap_spinner(self) -> None:
        with self._lock:
            self._model_swap_spin += 1
        self._invalidate_prompt_app()

    def toggle_activity_expanded(self) -> None:
        self._activity_expanded = not self._activity_expanded
        self._invalidate_prompt_app()

    def _invalidate_prompt_app(self) -> None:
        try:
            from prompt_toolkit.application import get_app

            app = get_app()
            if app is not None:
                app.invalidate()
        except Exception:
            pass

    def _render_toolbar(self) -> Union[HTML, str]:
        with self._lock:
            phase = self._phase
            model = self._model_line
            task = self._task
            memory = self._memory_line
            expanded = self._activity_expanded

        elapsed = time.monotonic() - self._session_start
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        elapsed_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        mode_hint = "trace" if expanded else "compact"

        if not self._caps.rich:
            parts = [phase]
            if model:
                parts.append(_trunc(model, 28))
            if task:
                parts.append(_trunc(task, 32))
            if memory:
                parts.append(_trunc(memory, 28))
            parts.append(f"[{mode_hint}]")
            parts.append(elapsed_str)
            spin_chars = "|/-\\"
            with self._lock:
                swap = self._model_swap_label
                si = self._model_swap_spin
            if swap:
                parts.append(_model_swap_mini_bar(si))
                parts.append(spin_chars[si % len(spin_chars)] + " " + _trunc(swap, 22))
            return " | ".join(parts)

        def _seg(s: str) -> str:
            return _esc_html(_trunc(s, 30))

        parts_html = [f"<b>{_esc_html(phase)}</b>"]
        if model:
            parts_html.append(_seg(model))
        if task:
            parts_html.append(f"<ansiblue>{_seg(task)}</ansiblue>")
        if memory:
            parts_html.append(f"<ansiyellow>{_seg(memory)}</ansiyellow>")
        parts_html.append(f"<ansibrightblack>[{mode_hint}]</ansibrightblack>")
        parts_html.append(f"<ansibrightblack>{elapsed_str}</ansibrightblack>")
        spin_unicode = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        spin_ascii = "|/-\\"
        with self._lock:
            swap = self._model_swap_label
            si = self._model_swap_spin
        if swap:
            seq = spin_unicode if self._caps.supports_unicode else spin_ascii
            ch = seq[si % len(seq)]
            parts_html.append(_model_swap_mini_bar_html(si))
            parts_html.append(
                "<ansibrightblack>·</ansibrightblack> "
                f"<ansicyan>{_esc_html(ch)}</ansicyan> "
                f"<ansibrightblack>{_esc_html(_trunc(swap, 22))}</ansibrightblack>"
            )
        sep = " <ansibrightblack>·</ansibrightblack> "
        return HTML(sep.join(parts_html))

    # ── prompt_toolkit session ───────────────────────────────────────────────

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-o")
        def _toggle(_event):
            self.toggle_activity_expanded()
            mode = "detailed trace" if self._activity_expanded else "compact"
            self._bus.put(RenderEvent(EventKind.INFO, {"msg": f"Activity view: {mode}"}))

        return kb

    def _ensure_session(self) -> PromptSession:
        if self._pt_session is not None:
            return self._pt_session

        hist = None
        if self._history_path:
            try:
                self._history_path.parent.mkdir(parents=True, exist_ok=True)
                hist = FileHistory(str(self._history_path))
            except Exception:
                pass

        if self._caps.rich:
            style = PTStyle.from_dict(
                {
                    "prompt":               f"bold {D['AI_SPARK_BRIGHT']}",
                    "rprompt":              D["TEXT_MUTED"],
                    "bottom-toolbar":       f"bg:{D['PANEL']} {D['TEXT_MUTED']}",
                    "bottom-toolbar.text":  D["TEXT_MUTED"],
                }
            )
        else:
            style = PTStyle.from_dict(
                {"prompt": "", "rprompt": "", "bottom-toolbar": "", "bottom-toolbar.text": ""}
            )

        self._pt_session = PromptSession(
            history=hist,
            completer=SlashCommandCompleter(),
            key_bindings=self._build_key_bindings(),
            style=style,
            bottom_toolbar=self._render_toolbar,
            complete_while_typing=False,
            enable_open_in_editor=False,
        )
        return self._pt_session

    def prompt_user(self) -> Optional[str]:
        session = self._ensure_session()
        try:
            rprompt: Any = (
                HTML(f"<ansibrightblack>LAO {__version__}</ansibrightblack>")
                if self._caps.rich
                else f"LAO {__version__}"
            )
            return session.prompt([("class:prompt", "  ❯ ")], rprompt=rprompt)
        except EOFError:
            return None
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - self._last_ctrl_c < 1.5:
                return None  # double Ctrl+C → exit
            self._last_ctrl_c = now
            self._console.print(
                "  Press Ctrl+C again to exit, or keep typing.",
            )
            return ""

    # ── status bar helpers ───────────────────────────────────────────────────

    def update_status(
        self,
        *,
        phase: Optional[str] = None,
        model: Optional[str] = None,
        task: Optional[str] = None,
        memory: Optional[str] = None,
    ) -> None:
        """Update status bar fields directly (no bus round-trip needed)."""
        with self._lock:
            if phase is not None:
                self._phase = phase
            if model is not None:
                self._model_line = model
            if task is not None:
                self._task = task
            if memory is not None:
                self._memory_line = memory

    def bell(self) -> None:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Layer 5 — LogBridge
# ─────────────────────────────────────────────────────────────────────────────

class LogBridge(logging.Handler):
    """
    Intercepts stdlib log records and routes them into the RenderBus as
    ACTIVITY events (or STATUS_UPDATE events for model/memory lines).
    """

    # Patterns that update status bar fields silently
    _MODEL_RE = re.compile(r"\[ModelManager\]\s*(.*)")
    _TASK_RE  = re.compile(
        r"Coding task #(\d+):\s*(.+?)\s*\(attempt\s*(\d+)/(\d+)\)"
    )
    _MEM_AVAIL_RE = re.compile(r"available=([\d.]+GB)")
    _MEM_TARGET_RE = re.compile(r"target=([\d.]+GB)")

    # Patterns to drop entirely (noise)
    _DROP_PATTERNS = (
        "HTTP Request:",
        "Local AI Agent Orchestrator",
        "Models:",
        "Per-plan project dirs:",
        "Configured total RAM:",
    )

    def __init__(self, bus: RenderBus, shell: TerminalShell) -> None:
        super().__init__(level=logging.INFO)
        self._bus = bus
        self._shell = shell
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            raw = self.format(record)
            self._route(raw.strip(), record.levelname)
        except Exception:
            self.handleError(record)

    def _route(self, m: str, level: str) -> None:
        if not m:
            return
        if any(p in m for p in self._DROP_PATTERNS):
            return
        # Pure separator lines
        if set(m) <= {"=", "─", "-", "━"}:
            return

        # ── MemoryGate ──────────────────────────────────────────────────────
        if "[MemoryGate]" in m:
            if "Waiting..." in m or "Waiting for" in m:
                mem = self._short_memory(m)
                self._shell.update_status(memory=mem)
                return
            if any(k in m for k in ("Pages cleared", "Timeout", "Swap growing")):
                self._shell.update_status(memory="")
            self._bus.put(RenderEvent(EventKind.ACTIVITY, {"msg": m, "level": level}))
            return

        # ── ModelManager ────────────────────────────────────────────────────
        mm = self._MODEL_RE.match(m)
        if mm:
            model_line = mm.group(1).strip()
            self._shell.update_status(model=model_line)
            if any(k in m for k in ("Loading", "Unloading", "JIT", "Confirmed loaded")):
                self._bus.put(RenderEvent(EventKind.ACTIVITY, {"msg": m, "level": level}))
            return

        # ── Coding task progress ─────────────────────────────────────────────
        tm = self._TASK_RE.search(m)
        if tm:
            self._shell.update_status(
                phase="Coder",
                task=f"#{tm.group(1)} {tm.group(2)}",
            )
            self._bus.put(RenderEvent(EventKind.ACTIVITY, {"msg": m, "level": level}))
            return

        # ── Phase labels ─────────────────────────────────────────────────────
        for prefix, phase in (
            ("[Architect]", "Architect"),
            ("[Coder]",     "Coder"),
            ("[Reviewer]",  "Reviewer"),
            ("[Pilot]",     "Pilot"),
        ):
            if m.startswith(prefix):
                self._shell.update_status(phase=phase)
                self._bus.put(RenderEvent(EventKind.ACTIVITY, {"msg": m, "level": level}))
                return

        # ── Misc notable lines ───────────────────────────────────────────────
        if any(k in m for k in ("[Tools]", "[State]", "New plan:", "Factory Status:",
                                 "Total tokens:", "Shutdown requested")):
            self._bus.put(RenderEvent(EventKind.ACTIVITY, {"msg": m, "level": level}))
            return

        # ── Warnings / errors always surface ────────────────────────────────
        if level in ("WARNING", "ERROR"):
            self._bus.put(RenderEvent(EventKind.ACTIVITY, {"msg": m, "level": level}))
            return

    @staticmethod
    def _short_memory(m: str) -> str:
        m = sanitize_for_terminal(m)
        am = re.search(r"available=([\d.]+GB)", m)
        tm = re.search(r"target=([\d.]+GB)", m)
        if am and tm:
            return f"Memory settling {am.group(1)} → {tm.group(1)}"
        if "need +" in m:
            return m.replace("[MemoryGate] ", "")[:60]
        return "Memory settling…"


# ─────────────────────────────────────────────────────────────────────────────
# Public façade — UnifiedUI
# ─────────────────────────────────────────────────────────────────────────────

class UnifiedUI:
    """
    Single persistent UI for the entire LAO session.

    Wires TerminalCapabilities → RenderBus → ViewComposer → TerminalShell
    and exposes the same public methods as the previous implementation so
    callers (runner.py, cli.py, pilot.py) need zero changes.
    """

    def __init__(
        self, *, history_path: Optional[Path] = None, skip_initial_banner: bool = False
    ) -> None:
        global _active_ui
        _active_ui = self

        self._caps = TerminalCapabilities.probe()
        self._bus = RenderBus()
        self._composer = ViewComposer(self._caps)
        self._shell = TerminalShell(
            self._caps,
            self._composer,
            self._bus,
            history_path=history_path,
            skip_initial_banner=skip_initial_banner,
        )

        self._log_bridge: Optional[LogBridge] = None
        self._session_start = time.monotonic()
        self._queue_ref: Optional[Callable[[], Any]] = None
        self._last_stats_snapshot: dict = {}

        # Façade-only fields (not in shell)
        self._plan = ""
        self._attempt = ""
        self._idle_hint = ""
        self._pilot_onboarding_shown = False
        self._pilot_phase_lock = threading.Lock()
        self._pilot_cancellable_phase = False

    def set_pilot_cancellable_phase(self, active: bool) -> None:
        """Pilot sets this around chat.completions.create and tool execution for SIGINT routing."""
        with self._pilot_phase_lock:
            self._pilot_cancellable_phase = bool(active)

    def is_pilot_cancellable_phase(self) -> bool:
        with self._pilot_phase_lock:
            return self._pilot_cancellable_phase

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._shell.start()  # prints banner directly, then starts patch_stdout
        self._attach_logging()

    def stop(self) -> None:
        global _active_ui
        self._detach_logging()
        self._shell.stop()
        _active_ui = None

    def set_queue_getter(self, fn: Callable[[], Any]) -> None:
        self._queue_ref = fn

    def note_model_swap_progress(self, label: str) -> None:
        self._shell.set_model_swap_status(label)

    def tick_model_swap_spinner(self) -> None:
        self._shell.bump_model_swap_spinner()

    def show_pilot_onboarding_if_needed(self, queue: Any) -> None:
        if self._pilot_onboarding_shown:
            return
        self._pilot_onboarding_shown = True
        lines = [
            "Pilot — you can chat, run tools, create plans, or type /resume for autopilot.",
            "Try: continue an active plan, describe a new goal, or /status for the queue.",
            "Diagnostics: /help  ·  exit: /exit or double Ctrl+C.",
        ]
        try:
            stats = queue.get_stats()
            plans = queue.get_plans()
            if stats and any(v for v in stats.values()):
                lines.insert(1, f"Queue snapshot: {', '.join(f'{k}={v}' for k, v in sorted(stats.items()) if v)}")
            if plans:
                active = [p for p in plans if p.get("status") != "completed"]
                if active:
                    lines.insert(1, f"Active plan: {active[-1].get('filename', '?')}")
        except Exception:
            pass
        self.show_info("\n".join(lines))

    # ── logging bridge ───────────────────────────────────────────────────────

    def _attach_logging(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.INFO)
        self._log_bridge = LogBridge(self._bus, self._shell)
        root.addHandler(self._log_bridge)
        for noisy in ("httpx", "httpcore", "openai", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    def _detach_logging(self) -> None:
        root = logging.getLogger()
        if self._log_bridge is not None:
            try:
                root.removeHandler(self._log_bridge)
            except ValueError:
                pass
            self._log_bridge = None

    # ── status updates ───────────────────────────────────────────────────────

    def update_status(
        self,
        *,
        phase: Optional[str] = None,
        plan: Optional[str] = None,
        task: Optional[str] = None,
        attempt: Optional[str] = None,
        model: Optional[str] = None,
        memory: Optional[str] = None,
        idle_hint: Optional[str] = None,
    ) -> None:
        # Keep façade fields in sync for backward-compat property access
        if plan is not None:
            self._plan = plan
        if attempt is not None:
            self._attempt = attempt
        if idle_hint is not None:
            self._idle_hint = idle_hint
        # Delegate to shell (updates toolbar fields directly, thread-safe)
        self._shell.update_status(phase=phase, model=model, task=task, memory=memory)

    # ── output methods (all route through RenderBus) ─────────────────────────

    def log_activity(self, msg: str, *, level: str = "INFO") -> None:
        self._bus.put(RenderEvent(EventKind.ACTIVITY, {"msg": msg, "level": level}))

    def show_user_message(self, content: str) -> None:
        self._bus.put(RenderEvent(EventKind.USER_MSG, {"content": content}))

    def show_assistant_message(self, content: str) -> None:
        self._bus.put(RenderEvent(EventKind.ASSISTANT_MSG, {"content": content}))

    def show_tool_call(self, name: str, args: dict) -> None:
        self._bus.put(RenderEvent(EventKind.TOOL_CALL, {"name": name, "args": args}))

    def show_tool_result(self, name: str, result: str) -> None:
        self._bus.put(RenderEvent(EventKind.TOOL_RESULT, {"name": name, "result": result}))

    def show_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self._bus.put(
            RenderEvent(EventKind.USAGE, {"prompt": prompt_tokens, "completion": completion_tokens})
        )

    def show_thinking(self, hint: str) -> None:
        self._bus.put(RenderEvent(EventKind.THINKING, {"hint": hint}))

    def show_transition(self, from_mode: str, to_mode: str) -> None:
        self._bus.put(
            RenderEvent(EventKind.TRANSITION, {"from_mode": from_mode, "to_mode": to_mode})
        )

    def show_report(self, title: str, rows: list[tuple[str, str]]) -> None:
        self._bus.put(RenderEvent(EventKind.REPORT, {"title": title, "rows": rows}))

    def show_info(self, msg: str) -> None:
        self._bus.put(RenderEvent(EventKind.INFO, {"msg": msg}))

    def show_error(self, msg: str, *, suggestion: str = "") -> None:
        self._bus.put(RenderEvent(EventKind.ERROR, {"msg": msg, "suggestion": suggestion}))

    def bell(self) -> None:
        self._shell.bell()

    # ── input ────────────────────────────────────────────────────────────────

    def prompt_user(self) -> Optional[str]:
        return self._shell.prompt_user()

    # ── activity detail toggle (Ctrl+O) ──────────────────────────────────────

    def toggle_activity_detail(self) -> None:
        self._shell.toggle_activity_expanded()

    # ── queue-aware reports ──────────────────────────────────────────────────

    def snapshot_stats(self) -> None:
        if self._queue_ref is not None:
            try:
                self._last_stats_snapshot = dict(self._queue_ref().get_stats())
            except Exception:
                pass

    def build_idle_report(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if self._queue_ref is None:
            return rows
        try:
            q = self._queue_ref()
            stats = q.get_stats()
            prev = self._last_stats_snapshot
            if stats:
                total = sum(stats.values())
                completed = stats.get("completed", 0)
                failed = stats.get("failed", 0)
                pending = stats.get("pending", 0)
                rows.append(("Tasks", f"{completed}/{total} completed"))
                if failed:
                    rows.append(("Failed", str(failed)))
                if pending:
                    rows.append(("Pending", str(pending)))
                delta = completed - prev.get("completed", 0)
                if delta > 0:
                    rows.append(("This run", f"+{delta} completed"))
            for p in q.get_plans():
                plan_tasks = q.get_plan_tasks(p["id"])
                failed_tasks = [t for t in plan_tasks if t.status == "failed"]
                rows.append(("Plan", f"{p['filename']} [{p['status']}]"))
                for t in failed_tasks[:3]:
                    rows.append(("  failed", f"#{t.id} {t.title} ({t.escalation_reason or 'unknown'})"))
            tokens = q.get_total_tokens()
            total_tok = tokens["prompt_tokens"] + tokens["completion_tokens"]
            if total_tok > 0:
                rows.append(("Tokens", f"{total_tok:,}"))
        except Exception:
            pass
        return rows

    def build_resume_report(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if self._queue_ref is None:
            return rows
        try:
            q = self._queue_ref()
            stats = q.get_stats()
            if stats:
                rows.append(("Pending", str(stats.get("pending", 0) + stats.get("coded", 0))))
            active = [p for p in q.get_plans() if p["status"] != "completed"]
            if active:
                rows.append(("Active plans", str(len(active))))
        except Exception:
            pass
        return rows

    def print_run_summary(self, queue: Any, model_metrics: dict[str, int] | None = None) -> None:
        try:
            stats = queue.get_stats()
            tokens = queue.get_total_tokens()
            efficiency = queue.get_efficiency_metrics()
            rows = [(st, str(c)) for st, c in sorted(stats.items())]
            rows.append(("Tokens", f"{tokens['prompt_tokens'] + tokens['completion_tokens']:,}"))
            rows.append(
                (
                    "Run-log model_key changes",
                    str(efficiency.get("model_switches", 0)),
                )
            )
            rows.append(("Run events", str(efficiency.get("run_events", 0))))
            if model_metrics:
                rows.append(
                    (
                        "LM Studio swap cycles",
                        str(model_metrics.get("swap_count", 0)),
                    )
                )
                rows.append(
                    ("LM Studio loads", str(model_metrics.get("load_count", 0)))
                )
                rows.append(
                    ("LM Studio unloads", str(model_metrics.get("unload_count", 0)))
                )
            self.show_report("LAO run finished", rows)
        except Exception:
            pass

    # ── legacy compat: proxy shell fields for tests and backward compat ──────

    @property
    def _supports_color(self) -> bool:
        return self._caps.supports_color

    @property
    def _console(self) -> Console:
        return self._shell._console

    @property
    def _phase(self) -> str:
        return self._shell._phase

    @_phase.setter
    def _phase(self, v: str) -> None:
        self._shell._phase = v

    @property
    def _task(self) -> str:
        return self._shell._task

    @_task.setter
    def _task(self, v: str) -> None:
        self._shell._task = v

    @property
    def _model_line(self) -> str:
        return self._shell._model_line

    @_model_line.setter
    def _model_line(self, v: str) -> None:
        self._shell._model_line = v

    @property
    def _memory_line(self) -> str:
        return self._shell._memory_line

    @_memory_line.setter
    def _memory_line(self, v: str) -> None:
        self._shell._memory_line = v

    @property
    def _activity_expanded(self) -> bool:
        return self._shell._activity_expanded

    @_activity_expanded.setter
    def _activity_expanded(self, v: bool) -> None:
        self._shell._activity_expanded = v


# ─────────────────────────────────────────────────────────────────────────────
# apply_runner_context — called by runner.py and console_ui.py
# ─────────────────────────────────────────────────────────────────────────────

def apply_runner_context(
    *,
    phase: Optional[str] = None,
    plan: Optional[str] = None,
    task: Optional[str] = None,
    attempt: Optional[str] = None,
    idle_hint: Optional[str] = None,
) -> None:
    ui = get_unified_ui()
    if ui is not None:
        ui.update_status(phase=phase, plan=plan, task=task, attempt=attempt, idle_hint=idle_hint)


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat helpers (used by tests and legacy call-sites)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_color_support() -> bool:
    """Legacy helper — delegates to TerminalCapabilities."""
    return TerminalCapabilities.probe().supports_color


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")
