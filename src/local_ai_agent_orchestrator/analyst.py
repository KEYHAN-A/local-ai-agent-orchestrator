# SPDX-License-Identifier: GPL-3.0-or-later
"""
Analyst phase helpers: tiered workspace input assembly for the read-only
project analyst model.

The analyst receives a structured text snapshot of the workspace -- NOT raw
file dumps -- assembled in three tiers ordered by information density:

  Tier 1 (always):   directory tree, manifest files, import/require summary
  Tier 2 (budget):   first N lines of each source file (sorted by size)
  Tier 3 (targeted): files explicitly mentioned in the plan text

Token budget = context_length * max_context_utilization
              - max_completion - system_prompt_headroom

Tiers are filled in order until the budget is exhausted.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Extensions treated as source code for Tier-2 sampling.
_SOURCE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".swift",
    ".kt", ".java", ".c", ".cpp", ".h", ".cs", ".rb", ".php",
    ".sh", ".bash", ".zsh",
}
# Extensions treated as config / manifest.
_MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "cargo.toml", "go.mod", "go.sum", "pom.xml", "build.gradle",
    "package.swift", "podfile", "gemfile", "requirements.txt",
    "requirements-dev.txt", "pipfile", "pipfile.lock",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "makefile", "justfile",
}
# Directories to skip when walking the tree.
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", "dist", "build", ".build", ".cache", ".eggs",
    "*.egg-info", ".mypy_cache", ".ruff_cache", ".pytest_cache",
}

# Approximate chars-per-token ratio for the heuristic fallback.
_CHARS_PER_TOKEN = 3.5

# Headroom reserved for the system prompt + JSON output schema text.
_SYSTEM_HEADROOM_TOKENS = 600


def _estimate_tokens(text: str) -> int:
    """Rough token estimate without tiktoken dependency."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, int(len(text) / _CHARS_PER_TOKEN))


def _build_tree(workspace: Path, max_depth: int = 5) -> str:
    """Return an indented directory tree string."""
    lines: list[str] = [f"workspace: {workspace.name}/"]

    def _walk(path: Path, depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            connector = "├── " if entry != entries[-1] else "└── "
            lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                extension = "│   " if entry != entries[-1] else "    "
                _walk(entry, depth + 1, prefix + extension)

    _walk(workspace, 1, "")
    return "\n".join(lines)


def _collect_manifests(workspace: Path) -> list[tuple[Path, str]]:
    """Return (path, content) for known manifest files found in the workspace."""
    results: list[tuple[Path, str]] = []
    for p in workspace.rglob("*"):
        if p.is_file() and p.name.lower() in _MANIFEST_NAMES:
            # Skip deep node_modules / vendor dirs
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                results.append((p, content[:3000]))
            except OSError:
                pass
    return results


def _collect_import_summary(workspace: Path, max_files: int = 200) -> str:
    """
    Grep-style import/require/use summary across source files.
    Returns a compact multi-line string: one line per file with its imports.
    """
    lines: list[str] = []
    import_re = re.compile(
        r"^\s*(?:import|from|require|use|include|#include)\s+['\"]?([^\s'\"(;]+)",
        re.MULTILINE,
    )
    count = 0
    for p in sorted(workspace.rglob("*")):
        if count >= max_files:
            break
        if not p.is_file() or p.suffix not in _SOURCE_EXTS:
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        imports = import_re.findall(text[:8000])
        if imports:
            rel = str(p.relative_to(workspace))
            unique = list(dict.fromkeys(imports))[:20]
            lines.append(f"{rel}: {', '.join(unique)}")
            count += 1
    return "\n".join(lines)


def _collect_source_excerpts(
    workspace: Path,
    token_budget: int,
    lines_per_file: int = 60,
) -> str:
    """
    Tier-2: sample the first `lines_per_file` lines of each source file,
    largest-first, until the token budget is consumed.
    """
    source_files = []
    for p in workspace.rglob("*"):
        if p.is_file() and p.suffix in _SOURCE_EXTS:
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            try:
                source_files.append((p.stat().st_size, p))
            except OSError:
                pass
    source_files.sort(reverse=True)

    parts: list[str] = []
    used = 0
    for _, p in source_files:
        if used >= token_budget:
            break
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        excerpt_lines = text.splitlines()[:lines_per_file]
        excerpt = "\n".join(excerpt_lines)
        tok = _estimate_tokens(excerpt)
        if used + tok > token_budget:
            # Trim to fit
            remaining_chars = int((token_budget - used) * _CHARS_PER_TOKEN)
            excerpt = excerpt[:remaining_chars]
            tok = _estimate_tokens(excerpt)
        rel = str(p.relative_to(workspace))
        parts.append(f"### {rel}\n```\n{excerpt}\n```")
        used += tok
    return "\n\n".join(parts)


def _collect_plan_targeted_files(
    workspace: Path,
    plan_text: str,
    token_budget: int,
) -> str:
    """
    Tier-3: read files explicitly mentioned in the plan text that exist in
    the workspace, up to the remaining token budget.
    """
    path_re = re.compile(r"[`'\"]([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)[`'\"]")
    mentioned = list(dict.fromkeys(path_re.findall(plan_text)))
    parts: list[str] = []
    used = 0
    for rel_path in mentioned[:30]:
        if used >= token_budget:
            break
        candidate = workspace / rel_path
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        tok = _estimate_tokens(content)
        if used + tok > token_budget:
            remaining_chars = int((token_budget - used) * _CHARS_PER_TOKEN)
            content = content[:remaining_chars]
            tok = _estimate_tokens(content)
        parts.append(f"### {rel_path}\n```\n{content}\n```")
        used += tok
    return "\n\n".join(parts)


def build_analyst_input(
    workspace: Path,
    plan_text: str,
    context_length: int,
    max_completion: int,
    max_context_utilization: float = 0.85,
) -> str:
    """
    Assemble a tiered workspace snapshot for the analyst model.

    Returns a single string ready to embed in the analyst user message.
    The total token estimate stays within:
        context_length * max_context_utilization - max_completion - _SYSTEM_HEADROOM_TOKENS
    """
    total_budget = int(
        context_length * max_context_utilization
    ) - max_completion - _SYSTEM_HEADROOM_TOKENS
    total_budget = max(512, total_budget)

    sections: list[str] = []
    used = 0

    # ── Tier 1: tree + manifests + import summary ─────────────────────
    tree = _build_tree(workspace)
    tree_tok = _estimate_tokens(tree)
    if used + tree_tok <= total_budget:
        sections.append(f"## Directory Tree\n```\n{tree}\n```")
        used += tree_tok

    manifests = _collect_manifests(workspace)
    for mpath, mcontent in manifests:
        rel = str(mpath.relative_to(workspace))
        block = f"## Manifest: {rel}\n```\n{mcontent}\n```"
        tok = _estimate_tokens(block)
        if used + tok > total_budget:
            break
        sections.append(block)
        used += tok

    import_summary = _collect_import_summary(workspace)
    if import_summary:
        block = f"## Import Summary\n```\n{import_summary}\n```"
        tok = _estimate_tokens(block)
        if used + tok <= total_budget:
            sections.append(block)
            used += tok

    # ── Tier 2: source excerpts ───────────────────────────────────────
    tier2_budget = total_budget - used
    if tier2_budget > 200:
        excerpts = _collect_source_excerpts(workspace, tier2_budget)
        if excerpts:
            block = f"## Source Excerpts (first 60 lines per file)\n\n{excerpts}"
            tok = _estimate_tokens(block)
            if used + tok <= total_budget:
                sections.append(block)
                used += tok

    # ── Tier 3: plan-targeted files ───────────────────────────────────
    tier3_budget = total_budget - used
    if tier3_budget > 200 and plan_text:
        targeted = _collect_plan_targeted_files(workspace, plan_text, tier3_budget)
        if targeted:
            block = f"## Plan-Referenced Files\n\n{targeted}"
            tok = _estimate_tokens(block)
            if used + tok <= total_budget:
                sections.append(block)
                used += tok

    log.info(
        "[Analyst] Input assembled: %d chars, ~%d tokens (budget %d)",
        sum(len(s) for s in sections),
        used,
        total_budget,
    )
    return "\n\n".join(sections)


def parse_analyst_report(content: str) -> Optional[dict]:
    """
    Parse the analyst's JSON output.  Returns the dict or None on failure.
    Strips thinking blocks and markdown fences before parsing.
    """
    import json

    # Strip <think>...</think>
    content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
    # Strip markdown fences
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        content = content.strip()
    # Find first { ... }
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(content[start:], start):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(content[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
