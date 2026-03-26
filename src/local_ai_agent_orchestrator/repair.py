"""Deterministic repair payload builder from structured findings."""

from __future__ import annotations

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
    return "\n".join(lines)

