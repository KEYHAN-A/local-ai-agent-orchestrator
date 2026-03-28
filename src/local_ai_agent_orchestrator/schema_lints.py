# SPDX-License-Identifier: GPL-3.0-or-later
"""Generalized schema-safety lint hooks."""

from __future__ import annotations

import re
from pathlib import Path


def _strip_swift_comments_and_strings(text: str) -> str:
    """
    Remove // and /* */ comments and string literals so heuristic scans
    do not flag markers inside documentation or sample code.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if i + 1 < n and ch == "/" and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\n\r":
                i += 1
            out.append(" ")
            continue
        if i + 1 < n and ch == "/" and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            out.append(" ")
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            if i < n and quote == "\"" and text[i] == "\"":
                if i + 1 < n and text[i + 1] == "\"":
                    i += 3
                    while i + 2 < n and not (text[i] == "\"" and text[i + 1] == "\"" and text[i + 2] == "\""):
                        i += 1
                    i = min(i + 3, n)
                else:
                    while i < n and text[i] != "\"":
                        if text[i] == "\\" and i + 1 < n:
                            i += 2
                            continue
                        i += 1
                    i = min(i + 1, n)
                out.append(" ")
                continue
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def run_schema_lints(path: str, text: str) -> list[dict]:
    out: list[dict] = []
    scan_text = text
    if path.lower().endswith(".swift"):
        scan_text = _strip_swift_comments_and_strings(text)

    patterns = [
        ("untyped_any_map", r"\[String\s*:\s*Any\]"),
        ("untyped_any", r"(?<![\w.]):\s*Any\b"),
    ]
    for issue_class, pat in patterns:
        if re.search(pat, scan_text):
            out.append(
                {
                    "severity": "major",
                    "issue_class": issue_class,
                    "file_path": path,
                    "message": f"Detected risky untyped schema marker matching `{pat}`.",
                    "fix_hint": "Replace with typed models, tagged unions, or custom serialization wrappers.",
                    "analyzer_id": "schema_lints",
                    "analyzer_kind": "heuristic",
                    "confidence": 0.82,
                }
            )
    return out


def should_lint_file(p: Path) -> bool:
    return p.suffix.lower() in {".swift", ".ts", ".tsx", ".js", ".py", ".json"}
