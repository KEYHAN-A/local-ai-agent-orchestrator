"""Validation helpers for post-coder quality gates."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.analyzers import run_registered_analyzers
from local_ai_agent_orchestrator.consistency import run_consistency_checks
from local_ai_agent_orchestrator.schema_lints import (
    _strip_swift_comments_and_strings,
    run_schema_lints,
    should_lint_file,
)
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
    analyzer_id: str = "builtin"
    analyzer_kind: str = "heuristic"
    confidence: float = 0.6


def extract_written_files(coder_output: str) -> list[str]:
    m = re.search(r"Files written:\s*(.+)", coder_output or "")
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(",") if p.strip()]


def validate_files(
    workspace: Path,
    file_paths: list[str],
    on_validation_command_result=None,
) -> list[Finding]:
    findings: list[Finding] = []
    plan_langs = infer_plan_languages(workspace)
    total_markers = 0
    total_chars = 0
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
                    analyzer_id="file_existence",
                    analyzer_kind="runtime",
                    confidence=0.99,
                )
            )
            continue
        if p.is_dir():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        f, markers = _placeholder_findings(rel, text)
        total_markers += markers
        total_chars += len(text)
        findings.extend(f)
        findings.extend(_codable_findings(rel, text))
        if should_lint_file(p):
            findings.extend(_from_dicts(run_schema_lints(rel, text)))
        findings.extend(_from_analyzer_results(run_registered_analyzers(p, text)))
        if p.suffix.lower() in {".proj", ".workspace"}:
            findings.extend(_synthetic_project_graph_findings(rel, text))
    findings.extend(_placeholder_ratio_findings(total_markers, total_chars))
    findings.extend(validate_cross_file_consistency(workspace, plan_langs))
    findings.extend(_from_dicts(run_consistency_checks(workspace)))
    findings.extend(
        run_optional_validation_commands(
            workspace,
            plan_langs,
            on_command_result=on_validation_command_result,
        )
    )
    if file_paths and any(rel.lower().endswith(".swift") for rel in file_paths):
        findings.extend(_ios_swift_manifest_findings(workspace, plan_langs))
    return findings


def _placeholder_findings(path: str, text: str) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    low = text.lower()
    markers = 0
    for pat in PLACEHOLDER_PATTERNS:
        if re.search(pat, low, re.IGNORECASE):
            markers += 1
            out.append(
                Finding(
                    severity="major",
                    issue_class="placeholder_text",
                    file_path=path,
                    message=f"Detected placeholder marker matching /{pat}/.",
                    fix_hint="Replace placeholder logic with concrete implementation.",
                    analyzer_id="placeholder_scan",
                    analyzer_kind="heuristic",
                    confidence=0.72,
                )
            )
    return out, markers


def _placeholder_ratio_findings(total_markers: int, total_chars: int) -> list[Finding]:
    if total_chars <= 0:
        return []
    try:
        settings = get_settings()
        max_per_kloc = settings.placeholder_max_markers_per_kloc
        max_ratio = settings.placeholder_max_ratio
    except RuntimeError:
        max_per_kloc = 3.0
        max_ratio = 0.02
    kloc = max(0.001, total_chars / 4000.0)
    markers_per_kloc = total_markers / kloc
    marker_ratio = total_markers / max(1.0, total_chars / 100.0)
    out: list[Finding] = []
    if markers_per_kloc > max_per_kloc:
        out.append(
            Finding(
                severity="major",
                issue_class="placeholder_density",
                message=(
                    f"Placeholder density too high ({markers_per_kloc:.2f}/KLOC), "
                    f"threshold is {max_per_kloc:.2f}."
                ),
                fix_hint="Regenerate affected files with concrete implementation details.",
                analyzer_id="placeholder_density",
                analyzer_kind="heuristic",
                confidence=0.78,
            )
        )
    if marker_ratio > max_ratio:
        out.append(
            Finding(
                severity="major",
                issue_class="placeholder_ratio",
                message=(
                    f"Placeholder ratio too high ({marker_ratio:.4f}), "
                    f"threshold is {max_ratio:.4f}."
                ),
                fix_hint="Reduce scaffold text and complete implementation bodies.",
                analyzer_id="placeholder_density",
                analyzer_kind="heuristic",
                confidence=0.78,
            )
        )
    return out


def _codable_findings(path: str, text: str) -> list[Finding]:
    probe = text
    if path.lower().endswith(".swift"):
        probe = _strip_swift_comments_and_strings(text)
    if re.search(r"\[String\s*:\s*Any\]", probe) and re.search(r"\bCodable\b", probe):
        return [
            Finding(
                severity="major",
                issue_class="codable_any",
                file_path=path,
                message="`[String: Any]` used near a Codable type.",
                fix_hint="Use typed models, enum payloads, or a custom codable wrapper.",
                analyzer_id="swift_codable_scan",
                analyzer_kind="heuristic",
                confidence=0.8,
            )
        ]
    return []


def _ios_swift_manifest_findings(workspace: Path, plan_langs: set[str]) -> list[Finding]:
    """Advisory when Swift sources exist but no Xcode/SPM manifest is present."""
    if "swift" not in plan_langs:
        return []
    try:
        swift_files = [p for p in workspace.rglob("*.swift") if p.is_file()]
    except OSError:
        return []
    if not swift_files:
        return []
    if (workspace / "Package.swift").is_file():
        return []
    try:
        has_xcode = any(
            p.is_dir() and p.suffix == ".xcodeproj"
            for p in workspace.iterdir()
        )
    except OSError:
        has_xcode = False
    if has_xcode:
        return []
    return [
        Finding(
            severity="minor",
            issue_class="missing_ios_manifest",
            file_path=None,
            message=(
                "Swift sources found but no Package.swift or .xcodeproj at workspace root; "
                "the tree may not be buildable until you add one."
            ),
            fix_hint=(
                "Add an Xcode project or Swift Package manifest, then set "
                "orchestration.validation_build_cmd in factory.yaml (see docs/CONFIGURATION.md)."
            ),
            analyzer_id="ios_manifest_scan",
            analyzer_kind="heuristic",
            confidence=0.75,
        )
    ]


def _synthetic_project_graph_findings(path: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    if "..." in text:
        out.append(
            Finding(
                severity="critical",
                issue_class="synthetic_project_graph",
                file_path=path,
                message="Project metadata file appears synthetic (contains ellipsis placeholders).",
                fix_hint="Generate a real project graph and verify all references exist.",
                analyzer_id="project_graph_scan",
                analyzer_kind="heuristic",
                confidence=0.9,
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
                analyzer_id="reviewer_model",
                analyzer_kind="llm",
                confidence=0.65,
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
                        analyzer_id="python_symbol_scan",
                        analyzer_kind="heuristic",
                        confidence=0.58,
                    )
                )
    return findings


def run_optional_validation_commands(
    workspace: Path,
    plan_langs: set[str],
    on_command_result=None,
) -> list[Finding]:
    try:
        settings = get_settings()
    except RuntimeError:
        return []
    findings: list[Finding] = []
    profile = settings.validation_profiles.get(
        settings.validation_profile,
        settings.validation_profiles.get("default", {"commands": []}),
    )
    cmd_rows: list[tuple[str, str]] = []
    for row in profile.get("commands", []) or []:
        if isinstance(row, dict) and row.get("command"):
            cmd_rows.append((str(row.get("kind", "contract")), str(row["command"])))
    if settings.validation_build_cmd:
        cmd_rows.append(("build", settings.validation_build_cmd))
    if settings.validation_lint_cmd:
        cmd_rows.append(("lint", settings.validation_lint_cmd))

    kinds_present = {k for k, _ in cmd_rows}
    existing_cmds = {c.strip() for _, c in cmd_rows if c}
    if settings.infer_validation_commands:
        ib, il = infer_validation_commands(workspace, plan_langs)
        if ib and ib.strip() not in existing_cmds and "build" not in kinds_present:
            cmd_rows.append(("build", ib.strip()))
            existing_cmds.add(ib.strip())
            kinds_present.add("build")
        if il and il.strip() not in existing_cmds and "lint" not in kinds_present:
            cmd_rows.append(("lint", il.strip()))
            existing_cmds.add(il.strip())
            kinds_present.add("lint")

    for kind, cmd in cmd_rows:
        if not cmd:
            continue
        started_at = datetime.now(timezone.utc).isoformat()
        rc, out = _run_cmd(cmd, workspace)
        finished_at = datetime.now(timezone.utc).isoformat()
        if callable(on_command_result):
            on_command_result(
                kind=kind,
                command=cmd,
                return_code=rc,
                output=out,
                started_at=started_at,
                finished_at=finished_at,
            )
        if rc != 0:
            findings.append(
                Finding(
                    severity="major",
                    issue_class=f"{kind}_command_failed",
                    message=f"Validation {kind} command failed: {cmd}",
                    fix_hint=(out[:400] if out else "Inspect command output and fix issues."),
                    analyzer_id=f"command:{kind}",
                    analyzer_kind="runtime",
                    confidence=0.97,
                )
            )
    return findings


def _from_dicts(rows: list[dict]) -> list[Finding]:
    out: list[Finding] = []
    for r in rows:
        out.append(
            Finding(
                severity=str(r.get("severity", "minor")),
                issue_class=str(r.get("issue_class", "validator_issue")),
                message=str(r.get("message", "")),
                file_path=r.get("file_path"),
                fix_hint=r.get("fix_hint"),
                analyzer_id=str(r.get("analyzer_id", "external")),
                analyzer_kind=str(r.get("analyzer_kind", "heuristic")),
                confidence=float(r.get("confidence", 0.6)),
            )
        )
    return out


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


def _from_analyzer_results(rows) -> list[Finding]:
    out: list[Finding] = []
    for r in rows:
        out.append(
            Finding(
                severity=str(getattr(r, "severity", "minor")),
                issue_class=str(getattr(r, "issue_class", "analyzer_issue")),
                message=str(getattr(r, "message", "")),
                file_path=getattr(r, "file_path", None),
                fix_hint=getattr(r, "fix_hint", None),
                analyzer_id=str(getattr(r, "analyzer_id", "external")),
                analyzer_kind=str(getattr(r, "analyzer_kind", "heuristic")),
                confidence=float(getattr(r, "confidence", 0.6)),
            )
        )
    return out


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


_SKIP_DIR_PARTS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        "dist",
        "build",
        ".eggs",
        ".mypy_cache",
        ".ruff_cache",
    }
)


def _path_has_skip_dir(path: Path) -> bool:
    return bool(_SKIP_DIR_PARTS.intersection(path.parts))


def _find_python_test_files(workspace: Path) -> bool:
    for pattern in ("test_*.py", "*_test.py"):
        for p in workspace.glob(f"**/{pattern}"):
            if p.is_file() and not _path_has_skip_dir(p):
                return True
    return False


def _python_compileall_targets(workspace: Path) -> list[str]:
    rels: list[str] = []
    for name in ("src", "lib", "app", "tests", "test"):
        d = workspace / name
        if d.is_dir() and any(d.rglob("*.py")):
            rels.append(name)
    if rels:
        return rels
    if any(workspace.glob("*.py")):
        return ["."]
    return []


def _pyproject_mentions_ruff(text: str) -> bool:
    t = text.lower()
    return "[tool.ruff]" in t or "ruff>" in t or "ruff =" in t or '"ruff"' in t


def _infer_python_commands(workspace: Path) -> tuple[str | None, str | None]:
    build_cmd: str | None = None
    lint_cmd: str | None = None
    pyproject = workspace / "pyproject.toml"
    toml_text = ""
    if pyproject.is_file():
        try:
            toml_text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            toml_text = ""

    if _find_python_test_files(workspace):
        build_cmd = "pytest -q --tb=short --maxfail=3"
    else:
        targets = _python_compileall_targets(workspace)
        if targets:
            build_cmd = "python -m compileall -q " + " ".join(targets)

    if toml_text and _pyproject_mentions_ruff(toml_text):
        lint_cmd = "ruff check ."
    return build_cmd, lint_cmd


def _node_script_invocation(workspace: Path, script_name: str) -> str:
    if (workspace / "pnpm-lock.yaml").is_file():
        return f"pnpm run {script_name}"
    if (workspace / "yarn.lock").is_file():
        return f"yarn {script_name}"
    return f"npm run {script_name}"


def _infer_node_commands(workspace: Path) -> tuple[str | None, str | None]:
    pkg = workspace / "package.json"
    if not pkg.is_file():
        return None, None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None, None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return None, None

    def pick_script(*names: str) -> str | None:
        for n in names:
            v = scripts.get(n)
            if isinstance(v, str) and v.strip():
                return n
        return None

    build_name = pick_script("build", "compile", "test", "check")
    lint_name = pick_script("lint", "eslint")
    build_cmd = _node_script_invocation(workspace, build_name) if build_name else None
    lint_cmd = _node_script_invocation(workspace, lint_name) if lint_name else None
    return build_cmd, lint_cmd


def _infer_go_commands(workspace: Path) -> tuple[str | None, str | None]:
    if not (workspace / "go.mod").is_file():
        return None, None
    return "go build ./...", "go vet ./..."


def _infer_rust_commands(workspace: Path) -> tuple[str | None, str | None]:
    if not (workspace / "Cargo.toml").is_file():
        return None, None
    return "cargo check", "cargo clippy -q"


def _infer_swift_commands(workspace: Path) -> tuple[str | None, str | None]:
    if not (workspace / "Package.swift").is_file():
        return None, None
    return "swift build", "swiftlint lint --quiet"


def infer_validation_commands(workspace: Path, langs: set[str]) -> tuple[str | None, str | None]:
    """
    Infer conservative build/lint shell commands from common project manifests.

    When multiple stacks are present, prefers manifests whose language appears in
    ``langs`` (from the plan); otherwise uses a fixed manifest priority order.
    """
    if not workspace.is_dir():
        return (None, None)

    rows: list[tuple[int, str | None, str | None]] = []

    def push(priority: int, b: str | None, l: str | None) -> None:
        if b or l:
            rows.append((priority, b, l))

    if (workspace / "package.json").is_file():
        b, l = _infer_node_commands(workspace)
        pri = 0 if langs & {"javascript", "typescript"} else 30
        push(pri, b, l)

    if (workspace / "pyproject.toml").is_file() or (workspace / "setup.py").is_file():
        b, l = _infer_python_commands(workspace)
        pri = 0 if "python" in langs else 20
        push(pri, b, l)

    if (workspace / "go.mod").is_file():
        b, l = _infer_go_commands(workspace)
        pri = 0 if "go" in langs else 40
        push(pri, b, l)

    if (workspace / "Cargo.toml").is_file():
        b, l = _infer_rust_commands(workspace)
        pri = 0 if "rust" in langs else 50
        push(pri, b, l)

    if (workspace / "Package.swift").is_file():
        b, l = _infer_swift_commands(workspace)
        pri = 0 if "swift" in langs else 60
        push(pri, b, l)

    if not rows:
        return (None, None)

    rows.sort(key=lambda r: r[0])
    best_build: str | None = None
    best_lint: str | None = None
    for _, b, l in rows:
        if best_build is None and b:
            best_build = b
        if best_lint is None and l:
            best_lint = l
        if best_build and best_lint:
            break
    return (best_build, best_lint)


def score_plan_languages(text: str) -> dict[str, int]:
    markers: dict[str, tuple[str, ...]] = {
        "python": ("python", "fastapi", "django", "flask", "pydantic", "pytest"),
        "typescript": ("typescript", "tsx", "tsconfig", "type-safe", "nestjs", "next.js"),
        "javascript": ("javascript", "node.js", "node ", "express", "react", "vite"),
        "swift": ("swift", "swift package", "swiftpm", "codable", "async let"),
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
