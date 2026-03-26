"""Analyzer interface and built-in analyzer registry."""

from __future__ import annotations

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

