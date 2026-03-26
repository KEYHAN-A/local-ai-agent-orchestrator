"""Analyzer interface and built-in analyzer registry."""

from __future__ import annotations

import json
import py_compile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class AnalyzerResult:
    severity: str
    issue_class: str
    message: str
    file_path: str | None = None
    fix_hint: str | None = None
    analyzer_id: str = "builtin"
    analyzer_kind: str = "heuristic"
    confidence: float = 0.6


AnalyzerFn = Callable[[Path, str], list[AnalyzerResult]]


def run_registered_analyzers(path: Path, text: str) -> list[AnalyzerResult]:
    out: list[AnalyzerResult] = []
    for fn in _default_analyzers_for_suffix(path.suffix.lower()):
        out.extend(fn(path, text))
    return out


def _default_analyzers_for_suffix(suffix: str) -> list[AnalyzerFn]:
    if suffix == ".py":
        return [_python_compile_analyzer]
    if suffix in {".ts", ".tsx"}:
        return [_typescript_structure_analyzer]
    if suffix == ".json":
        return [_json_structure_analyzer]
    return []


def _python_compile_analyzer(path: Path, text: str) -> list[AnalyzerResult]:
    try:
        py_compile.compile(str(path), doraise=True)
        return []
    except Exception as e:
        return [
            AnalyzerResult(
                severity="major",
                issue_class="python_compile_error",
                file_path=path.name,
                message=f"Python compile check failed: {e}",
                fix_hint="Fix Python syntax/indentation errors and re-run validation.",
                analyzer_id="python_py_compile",
                analyzer_kind="compiler",
                confidence=0.98,
            )
        ]


def _typescript_structure_analyzer(path: Path, text: str) -> list[AnalyzerResult]:
    # Lightweight AST-like structural check: balanced delimiters and no dangling template markers.
    pairs = {"{": "}", "(": ")", "[": "]"}
    closes = {v: k for k, v in pairs.items()}
    stack: list[str] = []
    for ch in text:
        if ch in pairs:
            stack.append(ch)
        elif ch in closes:
            if not stack or stack[-1] != closes[ch]:
                return [
                    AnalyzerResult(
                        severity="major",
                        issue_class="typescript_unbalanced_delimiters",
                        file_path=path.name,
                        message=f"Unbalanced delimiter found in {path.name}.",
                        fix_hint="Fix mismatched braces/parentheses/brackets.",
                        analyzer_id="typescript_structure",
                        analyzer_kind="ast",
                        confidence=0.84,
                    )
                ]
            stack.pop()
    if stack:
        return [
            AnalyzerResult(
                severity="major",
                issue_class="typescript_unbalanced_delimiters",
                file_path=path.name,
                message=f"Unclosed delimiter found in {path.name}.",
                fix_hint="Close all opened braces/parentheses/brackets.",
                analyzer_id="typescript_structure",
                analyzer_kind="ast",
                confidence=0.84,
            )
        ]
    if text.count("${") > text.count("}"):
        return [
            AnalyzerResult(
                severity="major",
                issue_class="typescript_template_syntax",
                file_path=path.name,
                message=f"Template string interpolation appears malformed in {path.name}.",
                fix_hint="Fix malformed `${...}` template interpolation blocks.",
                analyzer_id="typescript_structure",
                analyzer_kind="ast",
                confidence=0.76,
            )
        ]
    return []


def _json_structure_analyzer(path: Path, text: str) -> list[AnalyzerResult]:
    try:
        json.loads(text)
        return []
    except Exception as e:
        return [
            AnalyzerResult(
                severity="major",
                issue_class="json_parse_error",
                file_path=path.name,
                message=f"JSON parse check failed: {e}",
                fix_hint="Fix malformed JSON syntax (quotes, commas, brackets).",
                analyzer_id="json_structure",
                analyzer_kind="ast",
                confidence=0.94,
            )
        ]

