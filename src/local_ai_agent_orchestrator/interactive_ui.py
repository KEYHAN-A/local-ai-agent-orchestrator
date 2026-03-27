# SPDX-License-Identifier: GPL-3.0-or-later
"""
Shared interactive Rich UI primitives for LAO CLI flows.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence

import questionary
from questionary import Choice
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from local_ai_agent_orchestrator import __version__
from local_ai_agent_orchestrator.branding import ASCII_SPLASH, DISPLAY as D

_console = Console(force_terminal=True)


def is_tty() -> bool:
    return sys.stdout.isatty()


def print_splash(*, tagline: str | None = None) -> None:
    """Print LAO ASCII splash and optional tagline (TTY-only styling)."""
    lines = ASCII_SPLASH.split("\n")
    if not is_tty():
        print("\n".join(lines))
        if tagline:
            print(tagline)
        return
    for line in lines:
        _console.print(Text(line, style=D["AI_SPARK_BRIGHT"]))
    if tagline:
        _console.print()
        _console.print(Text(tagline, style=D["TEXT_MUTED"]))
    _console.print()


def select_option(
    title: str,
    choices: Sequence[tuple[str, str]],
    default_id: str,
) -> str:
    """
    Select one option by stable id. TTY: arrow keys + Enter via questionary.
    Non-TTY: numbered list and typed key (first char or full id where listed).
    """
    ids = [c[0] for c in choices]
    if default_id not in ids:
        default_id = ids[0]

    use_questionary = is_tty() and sys.stdin.isatty() and os.getenv("LAO_NO_TUI") != "1"
    if use_questionary:
        q_choices = [Choice(title=label, value=cid) for cid, label in choices]
        try:
            result = questionary.select(
                title,
                choices=q_choices,
                default=default_id,
                qmark="",
                style=questionary.Style(
                    [
                        ("highlighted", f"bold {D['AI_SPARK_BRIGHT']}"),
                        ("pointer", D["AI_SPARK"]),
                        ("selected", D["TEXT"]),
                        ("answer", f"bold {D['TEXT']}"),
                        ("question", D["TEXT_MUTED"]),
                    ]
                ),
            ).ask()
        except KeyboardInterrupt:
            raise
        if result is None:
            return "exit"
        return result

    # Fallback: legacy table + typed selection by numeric key or id
    key_to_id = {str(i + 1): cid for i, (cid, _) in enumerate(choices)}
    key_to_id.update({cid: cid for cid, _ in choices})
    if is_tty():
        tbl = Table(title=title, border_style=D["PANEL_ELEVATED"])
        tbl.add_column("Key", style=D["AI_SPARK_BRIGHT"], width=6)
        tbl.add_column("Action", style=D["TEXT"])
        for i, (cid, label) in enumerate(choices):
            key = str(i + 1)
            marker = " (default)" if cid == default_id else ""
            tbl.add_row(key, f"{label}{marker}")
        _console.print(tbl)
    else:
        print(title)
        for i, (cid, label) in enumerate(choices):
            key = str(i + 1)
            marker = " (default)" if cid == default_id else ""
            print(f"  {key}) {label}{marker}")

    valid = set(key_to_id)
    default_key = next(
        (k for k, v in key_to_id.items() if v == default_id and k.isdigit()),
        str(ids.index(default_id) + 1),
    )
    while True:
        picked = ask_text("Select", default_key).strip()
        if picked in key_to_id:
            return key_to_id[picked]
        # allow typing the id string directly
        if picked in ids:
            return picked
        print_info(f"Choose 1–{len(choices)} or one of: {', '.join(ids)}")


def print_header(title: str, subtitle: str | None = None) -> None:
    branded_title = f"{title} (v{__version__})"
    if not is_tty():
        print(branded_title)
        if subtitle:
            print(subtitle)
        return
    text = Text()
    text.append(" LAO ", style=f"bold {D['AI_SPARK_BRIGHT']}")
    text.append(branded_title, style=f"bold {D['TEXT']}")
    if subtitle:
        text.append(f"\n{subtitle}", style=D["TEXT_MUTED"])
    _console.print(
        Panel.fit(
            text,
            border_style=D["AI_SPARK"],
            style=f"on {D['BG']}",
            padding=(1, 2),
        )
    )


def print_status_table(title: str, rows: Sequence[tuple[str, str]]) -> None:
    if not is_tty():
        print(title)
        for k, v in rows:
            print(f"- {k}: {v}")
        return
    tbl = Table(title=title, border_style=D["PANEL_ELEVATED"])
    tbl.add_column("Item", style=D["TEXT_MUTED"])
    tbl.add_column("Status", style=D["TEXT"])
    for k, v in rows:
        tbl.add_row(k, v)
    _console.print(tbl)


def print_info(message: str) -> None:
    if not is_tty():
        print(message)
        return
    _console.print(Text(message, style=D["TEXT"]))


def print_note(message: str) -> None:
    if not is_tty():
        print(message)
        return
    _console.print(
        Panel(
            Text(message, style=D["TEXT"]),
            border_style=D["PANEL_ELEVATED"],
            style=f"on {D['BG']}",
            padding=(0, 1),
        )
    )


def print_warning(message: str) -> None:
    if not is_tty():
        print(f"WARNING: {message}")
        return
    _console.print(
        Panel(
            Text(message, style=D["WARNING_BRIGHT"]),
            border_style=D["WARNING_BRIGHT"],
            style=f"on {D['BG']}",
            padding=(0, 1),
        )
    )


def print_section(title: str) -> None:
    if not is_tty():
        print(f"\n{title}")
        return
    t = Text()
    t.append("• ", style=D["AI_SPARK"])
    t.append(title, style=f"bold {D['TEXT']}")
    _console.print(t)


def ask_text(prompt: str, default: str | None = None) -> str:
    try:
        if not is_tty():
            suffix = f" [{default}]" if default else ""
            raw = input(f"{prompt}{suffix}: ").strip()
            return raw if raw else (default or "")
        from rich.prompt import Prompt

        return Prompt.ask(prompt, default=default if default is not None else "")
    except KeyboardInterrupt:
        raise


def ask_float(prompt: str, default: float) -> float:
    while True:
        raw = ask_text(prompt, str(default))
        try:
            return float(raw)
        except ValueError:
            print_info("Please enter a valid number.")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    d = "y" if default else "n"
    while True:
        ans = ask_text(f"{prompt} (y/n)", d).strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print_info("Please answer y or n.")


def ask_choice(prompt: str, options: Sequence[tuple[str, str]], default_key: str) -> str:
    if is_tty():
        tbl = Table(title=prompt, border_style=D["PANEL_ELEVATED"])
        tbl.add_column("Key", style=D["AI_SPARK_BRIGHT"], width=6)
        tbl.add_column("Action", style=D["TEXT"])
        for key, label in options:
            marker = " (default)" if key == default_key else ""
            tbl.add_row(key, f"{label}{marker}")
        _console.print(tbl)
    else:
        print(prompt)
        for key, label in options:
            marker = " (default)" if key == default_key else ""
            print(f"  {key}) {label}{marker}")

    valid = {k for k, _ in options}
    while True:
        picked = ask_text("Select", default_key).strip()
        if picked in valid:
            return picked
        print_info(f"Choose one of: {', '.join(sorted(valid))}")


def print_goodbye(*, resume_command: str = "lao run") -> None:
    msg = (
        "Goodbye from LAO.\n"
        f"To continue later, run: {resume_command}\n"
        "Need setup/model checks first? Run: lao\n"
        "Website: https://lao.keyhan.info"
    )
    print_note(msg)
