# SPDX-License-Identifier: GPL-3.0-or-later
"""LAO brand color tokens (CLI, docs, site)."""

from __future__ import annotations

# Canonical palette
COLORS: dict[str, str] = {
    "PANEL": "#334155",
    "AI_SPARK": "#6366F1",
    "APPROVED": "#10B981",
    "WARNING": "#F97316",
    "BG": "#0F172A",
}

# Display-tuned (readability on dark surfaces)
DISPLAY: dict[str, str] = {
    **COLORS,
    "PANEL_ELEVATED": "#3F4D63",
    "AI_SPARK_BRIGHT": "#818CF8",
    "APPROVED_BRIGHT": "#34D399",
    "WARNING_BRIGHT": "#FB923C",
    "TEXT_MUTED": "#94A3B8",
    "TEXT": "#E2E8F0",
}

UPSTREAM_REPO = "https://github.com/KEYHAN-A/local-ai-agent-orchestrator"
AUTHOR = "KEYHAN"

# Splash art for interactive CLI (UTF-8 box drawing; avoid ANSI in the string).
ASCII_SPLASH = (
    "  ╭──────────────────────────────────────────╮\n"
    "  │   ▄▀▀  L O C A L   A I   O R C H .  ▀▄   │\n"
    "  │              ·  LAO  ·                   │\n"
    "  ╰──────────────────────────────────────────╯"
)
