"""Generalized schema-safety lint hooks."""

from __future__ import annotations

from pathlib import Path


def run_schema_lints(path: str, text: str) -> list[dict]:
    out: list[dict] = []
    patterns = [
        ("untyped_any_map", "[String: Any]"),
        ("untyped_any", ": Any"),
    ]
    low = text
    for issue_class, marker in patterns:
        if marker in low:
            out.append(
                {
                    "severity": "major",
                    "issue_class": issue_class,
                    "file_path": path,
                    "message": f"Detected risky untyped schema marker `{marker}`.",
                    "fix_hint": "Replace with typed models, tagged unions, or custom serialization wrappers.",
                    "analyzer_id": "schema_lints",
                    "analyzer_kind": "heuristic",
                    "confidence": 0.82,
                }
            )
    return out


def should_lint_file(p: Path) -> bool:
    return p.suffix.lower() in {".swift", ".ts", ".tsx", ".js", ".py", ".json"}
