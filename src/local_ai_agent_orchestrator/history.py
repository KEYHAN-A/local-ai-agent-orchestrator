"""History persistence utilities for KPI/dashboard trend tracking."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def append_history_entry(workspace: Path, filename: str, entry: dict, max_entries: int = 200) -> Path:
    out = workspace / filename
    existing: list[dict] = []
    if out.exists():
        try:
            raw = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = [x for x in raw if isinstance(x, dict)]
        except Exception:
            existing = []
    row = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **entry,
    }
    existing.append(row)
    if len(existing) > max_entries:
        existing = existing[-max_entries:]
    out.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return out

