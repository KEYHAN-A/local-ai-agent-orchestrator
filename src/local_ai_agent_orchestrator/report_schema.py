"""Quality report schema versioning and migration helpers."""

from __future__ import annotations

from copy import deepcopy

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

