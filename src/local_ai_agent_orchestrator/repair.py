"""Deterministic repair payload builder from structured findings."""

from __future__ import annotations

import hashlib
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


def is_no_progress_repeat(prev_feedback: str | None, new_feedback: str | None) -> bool:
    prev = extract_feedback_signature(prev_feedback or "")
    cur = extract_feedback_signature(new_feedback or "")
    return bool(prev and cur and prev == cur)

