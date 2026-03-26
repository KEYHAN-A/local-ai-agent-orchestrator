"""Deterministic repair payload builder from structured findings."""

from __future__ import annotations

import hashlib
import os
import re

from local_ai_agent_orchestrator.validators import Finding


_SEVERITY_ORDER = {"critical": 0, "blocker": 0, "major": 1, "minor": 2}


def build_repair_feedback(
    findings: list[Finding],
    *,
    contract_clause: str,
    summary_fallback: str = "",
    max_items: int = 20,
) -> str:
    if not findings:
        return summary_fallback.strip() or "Review rejected without structured findings."

    ranked = sorted(
        findings,
        key=lambda f: (
            _SEVERITY_ORDER.get((f.severity or "").lower(), 9),
            (f.file_path or ""),
            (f.issue_class or ""),
            (f.message or ""),
        ),
    )
    lines = [f"Contract clause: {contract_clause}", "Required fixes:"]
    for f in ranked[: max(1, max_items)]:
        lines.append(
            (
                f"- [{(f.severity or 'minor').lower()}] "
                f"{f.file_path or '-'} {f.issue_class}: {f.message}"
            ).strip()
        )
        if f.fix_hint:
            lines.append(f"  fix_hint: {f.fix_hint}")
    if len(ranked) > max_items:
        lines.append(f"- ... {len(ranked) - max_items} additional findings omitted")
    body = "\n".join(lines)
    sig = compute_feedback_signature(body)
    return f"finding_signature: {sig}\n{body}"


def compute_feedback_signature(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def extract_feedback_signature(text: str) -> str | None:
    m = re.search(r"finding_signature:\s*([a-f0-9]{12})", text or "", flags=re.IGNORECASE)
    return m.group(1).lower() if m else None


def is_no_progress_repeat(
    prev_feedback: str | None,
    new_feedback: str | None,
    prev_code_sig: str | None = None,
    new_code_sig: str | None = None,
) -> bool:
    """
    Check for no-progress between attempts.

    Returns True only if BOTH feedback AND code are unchanged:
    - If feedback signatures differ: not a no-progress loop (different issues found)
    - If feedback matches but code changed: not a no-progress loop (coder made changes)
    - If feedback matches and code unchanged: IS a no-progress loop

    Args:
        prev_feedback: Previous reviewer feedback text
        new_feedback: Current reviewer feedback text
        prev_code_sig: Previous code signature (optional, from task.code_signature)
        new_code_sig: Current code signature (optional, from compute_code_signature)
    """
    prev_fb = extract_feedback_signature(prev_feedback or "")
    cur_fb = extract_feedback_signature(new_feedback or "")

    # If feedback signatures don't match, it's not a repeat
    if not (prev_fb and cur_fb and prev_fb == cur_fb):
        return False

    # Feedback matches - check if code actually changed
    # If code signatures are provided and different, coder made progress
    if prev_code_sig and new_code_sig and prev_code_sig != new_code_sig:
        return False

    # Same feedback and no code change evidence = no progress
    return True


def compute_code_signature(file_paths: list[str], workspace: str | None = None) -> str:
    """
    Compute SHA-256 signature of actual file contents.

    This allows detecting if code actually changed between attempts,
    even if validation produces the same error messages.

    Args:
        file_paths: List of file paths to include in signature
        workspace: Optional workspace root to prepend to relative paths

    Returns:
        12-character hex signature, or empty string if no files exist
    """
    contents = []
    for path in file_paths:
        full_path = path
        if workspace and not os.path.isabs(path):
            full_path = os.path.join(workspace, path)
        try:
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    contents.append(f"=== {path} ===\n{f.read()}")
        except Exception:
            # Skip files that can't be read
            pass

    if not contents:
        return ""

    combined = "\n\n".join(contents)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:12]

