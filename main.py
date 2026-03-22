#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shim for running the CLI without pip install (adds src/ to path)."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from local_ai_agent_orchestrator.cli import main

if __name__ == "__main__":
    main()
