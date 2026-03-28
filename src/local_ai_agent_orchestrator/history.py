"""History persistence utilities for KPI/dashboard trend tracking."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def append_history_entry(workspace: Path, filename: str, entry: dict, max_entries: int = 200) -> Path:
    out = workspace / filename
    existing: list[dict] = []
    if out.exists():
        try:
            raw = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = [x for x in raw if isinstance(x, dict)]
            else:
                log.warning(
                    "[History] %s contained non-list JSON; starting fresh (old file backed up as %s.bak)",
                    filename,
                    filename,
                )
                out.rename(out.with_suffix(out.suffix + ".bak"))
        except json.JSONDecodeError as exc:
            log.warning(
                "[History] %s is corrupt (%s); starting fresh (old file backed up as %s.bak)",
                filename,
                exc,
                filename,
            )
            out.rename(out.with_suffix(out.suffix + ".bak"))
        except Exception as exc:
            log.warning("[History] Could not read %s: %s -- starting fresh", filename, exc)
    row = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **entry,
    }
    existing.append(row)
    if len(existing) > max_entries:
        existing = existing[-max_entries:]
    # Atomic write: write to a temp file in the same directory then os.replace.
    tmp_fd, tmp_path = tempfile.mkstemp(dir=workspace, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)
        os.replace(tmp_path, out)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return out

