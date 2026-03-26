"""Cross-file consistency analyzers with pluggable checks."""

from __future__ import annotations

import re
from pathlib import Path

def run_consistency_checks(workspace: Path) -> list[dict]:
    findings: list[dict] = []
    files = [p for p in workspace.rglob("*") if p.is_file()]
    rel_set = {str(p.relative_to(workspace)) for p in files}
    for p in files[:300]:
        rel = str(p.relative_to(workspace))
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.findall(r'["\']([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)["\']', text):
            if "/" in m and m not in rel_set:
                findings.append(
                    {
                        "severity": "minor",
                        "issue_class": "referenced_missing_file",
                        "file_path": rel,
                        "message": f"Referenced file path not found in workspace: {m}",
                        "fix_hint": "Create the referenced file or correct the path reference.",
                        "analyzer_id": "cross_file_reference",
                        "analyzer_kind": "heuristic",
                        "confidence": 0.66,
                    }
                )
    return findings
