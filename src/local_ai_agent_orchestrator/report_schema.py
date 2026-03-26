"""Quality report schema versioning and migration helpers."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

QUALITY_REPORT_SCHEMA_VERSION = "2.0.0"
QUALITY_REPORT_MIN_COMPATIBLE_VERSION = "2.0.0"


def build_report_meta() -> dict:
    return {
        "schema_version": QUALITY_REPORT_SCHEMA_VERSION,
        "min_compatible_version": QUALITY_REPORT_MIN_COMPATIBLE_VERSION,
    }


def migrate_quality_report(payload: dict) -> dict:
    """
    Best-effort migration path for older reports.
    Currently upgrades v1-like payloads to include report_meta.
    """
    out = deepcopy(payload or {})
    meta = out.get("report_meta")
    if not isinstance(meta, dict):
        out["report_meta"] = build_report_meta()
        return out

    if not meta.get("schema_version"):
        meta["schema_version"] = QUALITY_REPORT_SCHEMA_VERSION
    if not meta.get("min_compatible_version"):
        meta["min_compatible_version"] = QUALITY_REPORT_MIN_COMPATIBLE_VERSION
    out["report_meta"] = meta
    return out


def load_and_migrate_quality_report(path: Path, *, write_back: bool = False) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected report object in {path}, got {type(raw).__name__}")
    migrated = migrate_quality_report(raw)
    if write_back and migrated != raw:
        path.write_text(json.dumps(migrated, indent=2), encoding="utf-8")
    return migrated


def check_quality_report_schema(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"ok": False, "reason": "report_not_object", "path": str(path)}
    meta = raw.get("report_meta")
    if not isinstance(meta, dict):
        return {"ok": False, "reason": "missing_report_meta", "path": str(path)}
    if not meta.get("schema_version"):
        return {"ok": False, "reason": "missing_schema_version", "path": str(path)}
    if not meta.get("min_compatible_version"):
        return {"ok": False, "reason": "missing_min_compatible_version", "path": str(path)}
    return {"ok": True, "reason": "ok", "path": str(path), "report_meta": meta}

