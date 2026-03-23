"""Validation helpers for post-coder quality gates."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.settings import get_settings

PLACEHOLDER_PATTERNS = (
    r"\bfor now\b",
    r"\bplaceholder\b",
    r"\bin a real implementation\b",
    r"\bTODO\b",
)


@dataclass
class Finding:
    severity: str
    issue_class: str
    message: str
    file_path: str | None = None
    fix_hint: str | None = None


def extract_written_files(coder_output: str) -> list[str]:
    m = re.search(r"Files written:\s*(.+)", coder_output or "")
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(",") if p.strip()]


def validate_files(workspace: Path, file_paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    plan_langs = infer_plan_languages(workspace)
    for rel in file_paths:
        p = (workspace / rel).resolve()
        if not p.exists():
            findings.append(
                Finding(
                    severity="critical",
                    issue_class="missing_file",
                    file_path=rel,
                    message="File referenced by coder output does not exist.",
                    fix_hint="Write the file or remove it from coder summary.",
                )
            )
            continue
        if p.is_dir():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        findings.extend(_placeholder_findings(rel, text))
        findings.extend(_codable_findings(rel, text))
        if p.name == "project.pbxproj":
            findings.extend(_pbxproj_findings(rel, text))
    findings.extend(validate_cross_file_consistency(workspace, plan_langs))
    findings.extend(run_optional_validation_commands(workspace, plan_langs))
    return findings


def _placeholder_findings(path: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    low = text.lower()
    for pat in PLACEHOLDER_PATTERNS:
        if re.search(pat, low, re.IGNORECASE):
            out.append(
                Finding(
                    severity="major",
                    issue_class="placeholder_text",
                    file_path=path,
                    message=f"Detected placeholder marker matching /{pat}/.",
                    fix_hint="Replace placeholder logic with concrete implementation.",
                )
            )
    return out


def _codable_findings(path: str, text: str) -> list[Finding]:
    if "[String: Any]" in text and "Codable" in text:
        return [
            Finding(
                severity="major",
                issue_class="codable_any",
                file_path=path,
                message="`[String: Any]` used in a Codable type.",
                fix_hint="Use typed models, enum payloads, or a custom codable wrapper.",
            )
        ]
    return []


def _pbxproj_findings(path: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    if "..." in text:
        out.append(
            Finding(
                severity="critical",
                issue_class="synthetic_pbxproj",
                file_path=path,
                message="project.pbxproj appears synthetic (contains ellipsis placeholders).",
                fix_hint="Generate a real project graph and verify all references exist.",
            )
        )
    return out


def validate_reviewer_json(text: str) -> tuple[bool, list[Finding], str]:
    raw = (text or "").strip()
    candidates = [raw]
    if "```" in raw:
        fence_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
        candidates.extend(block.strip() for block in fence_blocks if block.strip())
    if "{" in raw and "}" in raw:
        start = raw.find("{")
        end = raw.rfind("}")
        if end > start:
            candidates.append(raw[start : end + 1].strip())

    data = None
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if isinstance(parsed, dict):
            data = parsed
            break

    if not isinstance(data, dict):
        return False, [], raw or "Reviewer output was not valid JSON."

    verdict = str(data.get("verdict", "")).upper()
    summary = str(data.get("summary", "")).strip()
    findings: list[Finding] = []
    for item in data.get("findings", []) or []:
        findings.append(
            Finding(
                severity=str(item.get("severity", "minor")).lower(),
                file_path=item.get("file_path"),
                issue_class=str(item.get("issue_class", "review_issue")),
                message=str(item.get("message", "")),
                fix_hint=item.get("fix_hint"),
            )
        )
    blocking_severities = {"critical", "major", "blocker"}
    has_blocker = any((f.severity or "").lower() in blocking_severities for f in findings)
    approved = verdict == "APPROVED" or (verdict == "REJECTED" and not has_blocker)
    return approved, findings, summary


def validate_cross_file_consistency(workspace: Path, plan_langs: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    # Advisory only: do not gate by inferred plan language.
    # If Python tests/files exist, run this consistency check regardless of plan hints.
    tests = list(workspace.rglob("test_*.py"))
    prod = [p for p in workspace.rglob("*.py") if p.name not in {t.name for t in tests}]
    if tests and prod:
        prod_text = "\n".join(
            p.read_text(encoding="utf-8", errors="replace")[:8000] for p in prod[:50]
        )
        symbols = set(re.findall(r"(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", prod_text))
        for t in tests[:50]:
            txt = t.read_text(encoding="utf-8", errors="replace")
            refs = set(re.findall(r"\b([A-Z][A-Za-z0-9_]{2,})\b", txt))
            missing = sorted([r for r in refs if r not in symbols and not r.startswith("Test")])
            for m in missing[:5]:
                findings.append(
                    Finding(
                        severity="minor",
                        issue_class="test_symbol_mismatch",
                        file_path=str(t.relative_to(workspace)),
                        message=f"Test references symbol '{m}' not found in scanned production symbols.",
                        fix_hint="Align tests with production names or add missing implementation.",
                    )
                )
    return findings


def run_optional_validation_commands(workspace: Path, plan_langs: set[str]) -> list[Finding]:
    try:
        settings = get_settings()
    except RuntimeError:
        return []
    findings: list[Finding] = []
    # Explicit-only policy:
    # do not auto-pick language/toolchain commands unless operator config specifies them.
    default_build, default_lint = (None, None)
    for kind, cmd in (
        ("build", settings.validation_build_cmd or default_build),
        ("lint", settings.validation_lint_cmd or default_lint),
    ):
        if not cmd:
            continue
        rc, out = _run_cmd(cmd, workspace)
        if rc != 0:
            findings.append(
                Finding(
                    severity="major",
                    issue_class=f"{kind}_command_failed",
                    message=f"Validation {kind} command failed: {cmd}",
                    fix_hint=(out[:400] if out else "Inspect command output and fix issues."),
                )
            )
    return findings


def _run_cmd(command: str, cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=180,
        )
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return p.returncode, out.strip()
    except Exception as e:
        return 1, str(e)


def infer_plan_languages(workspace: Path) -> set[str]:
    """
    Infer target languages from LAO_PLAN.md (plan snapshot), falling back
    to a small file-extension hint when the plan is unavailable.
    """
    langs: set[str] = set()
    plan_path = workspace / "LAO_PLAN.md"
    if plan_path.exists():
        text = plan_path.read_text(encoding="utf-8", errors="replace").lower()
        scores = score_plan_languages(text)
        langs = {lang for lang, score in scores.items() if score >= 1}
    if langs:
        return langs

    # Fallback: infer from dominant extensions when plan text is unavailable.
    exts = [p.suffix.lower() for p in workspace.rglob("*") if p.is_file()]
    langs.update(infer_languages_from_extensions(exts))
    return langs


def infer_validation_commands(workspace: Path, langs: set[str]) -> tuple[str | None, str | None]:
    """
    Reserved for future plan-provided explicit command extraction.
    Default behavior is non-prescriptive.
    """
    return (None, None)


def score_plan_languages(text: str) -> dict[str, int]:
    markers: dict[str, tuple[str, ...]] = {
        "python": ("python", "fastapi", "django", "flask", "pydantic", "pytest"),
        "typescript": ("typescript", "tsx", "tsconfig", "type-safe", "nestjs", "next.js"),
        "javascript": ("javascript", "node.js", "node ", "express", "react", "vite"),
        "swift": ("swift", "ios", "xcode", "swiftui", "uikit"),
        "go": ("golang", "go ", "gin", "fiber", "go.mod"),
        "rust": ("rust", "cargo", "tokio", "actix", "rocket"),
        "java": ("java", "spring", "gradle", "maven", "jvm"),
        "kotlin": ("kotlin", "ktor", "android", "jetpack compose", "kmp"),
        "csharp": ("c#", "dotnet", ".net", "asp.net", "blazor", "xunit"),
        "cpp": ("c++", "cpp", "cmake", "qt"),
        "c": ("ansi c", "embedded c", "c99", "c11"),
        "php": ("php", "laravel", "symfony", "composer"),
        "ruby": ("ruby", "rails", "sinatra", "bundler"),
        "elixir": ("elixir", "phoenix", "mix "),
        "scala": ("scala", "akka", "sbt", "play framework"),
        "haskell": ("haskell", "stack", "cabal"),
        "lua": ("lua", "luajit", "openresty"),
        "r": ("r language", "tidyverse", "shiny"),
        "matlab": ("matlab", "simulink"),
        "julia": ("julia", "pluto", "genie.jl"),
        "dart": ("dart", "flutter"),
        "objective-c": ("objective-c", "objc"),
        "perl": ("perl", "cpan"),
        "erlang": ("erlang", "otp", "rebar3"),
        "clojure": ("clojure", "leiningen", "clj"),
        "fsharp": ("f#", "fsharp"),
        "nim": ("nim", "nimble"),
        "zig": ("zig", "zig build"),
        "solidity": ("solidity", "evm", "hardhat", "foundry"),
    }
    scores: dict[str, int] = {}
    for lang, keys in markers.items():
        scores[lang] = sum(1 for k in keys if k in text)
    return scores


def infer_languages_from_extensions(exts: list[str]) -> set[str]:
    ext_map: dict[str, str] = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".swift": "swift",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".php": "php",
        ".rb": "ruby",
        ".ex": "elixir",
        ".exs": "elixir",
        ".scala": "scala",
        ".hs": "haskell",
        ".lua": "lua",
        ".r": "r",
        ".m": "objective-c",
        ".jl": "julia",
        ".dart": "dart",
        ".pl": "perl",
        ".erl": "erlang",
        ".clj": "clojure",
        ".fs": "fsharp",
        ".nim": "nim",
        ".zig": "zig",
        ".sol": "solidity",
    }
    out: set[str] = set()
    for ext in exts:
        lang = ext_map.get(ext)
        if lang:
            out.add(lang)
    return out
