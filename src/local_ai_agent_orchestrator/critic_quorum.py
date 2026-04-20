# SPDX-License-Identifier: GPL-3.0-or-later
"""N-model Critic Quorum.

Replaces or augments the single-model reviewer. The same prompt is sent to a
small pool of independent models (configurable per task ``risk``); their
verdicts are combined by majority. Findings are merged and deduped by
``(file_path, message)``.

Public surface::

    aggregate_critic_votes(votes)             -> dict
    quorum_size_for_risk(risk, base_size)     -> int
    pick_critic_models(models, n)             -> list[str]
    critic_quorum_phase(mm, queue, task, ...) -> dict
    review_task_with_critics(...)             -> dict | None
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from openai import OpenAI

from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.phases import _get_client, _llm_call, _strip_thinking_blocks
from local_ai_agent_orchestrator.prompts import build_critic_messages
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import MicroTask, TaskQueue
from local_ai_agent_orchestrator.validators import Finding, validate_reviewer_json

log = logging.getLogger(__name__)


_RISK_TO_QUORUM = {"low": 1, "med": 3, "high": 5}


def quorum_size_for_risk(risk: Optional[str], base_size: int) -> int:
    """Map ``risk`` ∈ {low, med, high} to a quorum size.

    The configured ``critic_quorum_size`` is the floor for med/high tasks.
    Low-risk tasks shrink to a single critic to save tokens. Returns at
    least 1.
    """
    base = max(1, int(base_size))
    if not risk:
        return base
    table = max(_RISK_TO_QUORUM.get(str(risk).strip().lower(), base), base if str(risk).strip().lower() != "low" else 1)
    return max(1, table)


def pick_critic_models(model_keys: list[str], n: int) -> list[str]:
    """Choose up to *n* distinct critic model keys, padding by rotation."""
    cleaned = [k for k in (model_keys or []) if isinstance(k, str) and k.strip()]
    if not cleaned:
        return []
    if len(cleaned) >= n:
        return cleaned[:n]
    out = list(cleaned)
    i = 0
    while len(out) < n:
        out.append(cleaned[i % len(cleaned)])
        i += 1
    return out


def _normalise_verdict(raw: str) -> str:
    v = (raw or "").strip().upper()
    if v in {"APPROVED", "APPROVE", "PASS", "OK", "GREEN", "ACCEPT"}:
        return "approved"
    if v in {"REJECTED", "REJECT", "FAIL", "RED", "DENY"}:
        return "rejected"
    return "rejected"


def aggregate_critic_votes(votes: list[dict]) -> dict:
    """Combine per-critic verdicts + findings into a single quorum decision.

    Each ``votes[i]`` is the per-model dict produced by ``critic_quorum_phase``::

        {"model": str, "verdict": "approved"|"rejected", "findings": [...], "summary": str}

    Returns::

        {
          "verdict": "approved" | "rejected",
          "n": int,
          "approve_count": int,
          "reject_count": int,
          "agreement_rate": float,    # winners / n
          "findings": [<deduped Finding-like dicts>],
          "votes": votes,
        }
    """
    n = len(votes)
    approve = sum(1 for v in votes if _normalise_verdict(v.get("verdict", "")) == "approved")
    reject = n - approve
    verdict = "approved" if approve > reject else "rejected"
    if approve == reject and n > 0:
        verdict = "rejected"
    agreement = (max(approve, reject) / n) if n else 0.0

    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for v in votes:
        for f in v.get("findings", []) or []:
            key = (
                str(f.get("file_path") or "").strip(),
                " ".join(str(f.get("message") or "").split()).lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "severity": str(f.get("severity") or "minor").lower(),
                "file_path": f.get("file_path"),
                "issue_class": str(f.get("issue_class") or "critic_issue"),
                "message": str(f.get("message") or ""),
                "fix_hint": f.get("fix_hint"),
                "source_model": v.get("model"),
            })

    return {
        "verdict": verdict,
        "n": n,
        "approve_count": approve,
        "reject_count": reject,
        "agreement_rate": round(agreement, 3),
        "findings": merged,
        "votes": votes,
    }


def _vote_one_critic(
    client: OpenAI,
    model_key: str,
    messages: list[dict],
    cfg,
) -> dict:
    """Run a single critic and return the parsed vote dict."""
    started = time.time()
    try:
        response = _llm_call(
            client,
            model_key,
            messages,
            max_tokens=min(getattr(cfg, "max_completion", 2048) or 2048, 2048),
            temperature=0.1,
            role="reviewer",
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:
        log.warning("[Critic] model %s raised: %s", model_key, exc)
        return {
            "model": model_key,
            "verdict": "rejected",
            "findings": [{
                "severity": "minor",
                "issue_class": "critic_error",
                "message": f"Critic call failed: {exc}",
                "file_path": None,
                "fix_hint": None,
            }],
            "summary": "(critic error)",
            "duration_s": round(time.time() - started, 3),
        }
    approved, findings, summary = validate_reviewer_json(_strip_thinking_blocks(content))
    return {
        "model": model_key,
        "verdict": "approved" if approved else "rejected",
        "findings": [
            {
                "severity": f.severity,
                "issue_class": f.issue_class,
                "message": f.message,
                "file_path": f.file_path,
                "fix_hint": f.fix_hint,
            }
            for f in findings
        ],
        "summary": summary,
        "duration_s": round(time.time() - started, 3),
    }


def critic_quorum_phase(
    mm: ModelManager,
    queue: TaskQueue,
    task: MicroTask,
    *,
    code_to_review: Optional[str] = None,
    acceptance_summary: Optional[str] = None,
    analyst_context: Optional[str] = None,
) -> dict:
    """Run the critic quorum for one task. Pure: does NOT mutate queue status.

    Persists the aggregated payload via :meth:`TaskQueue.set_task_critic_votes`.
    """
    s = get_settings()
    base_n = max(1, int(getattr(s, "critic_quorum_size", 3)))
    n = quorum_size_for_risk(task.risk, base_n)
    configured = list(getattr(s, "critic_models", []) or [])
    if not configured:
        configured = [s.models["reviewer"].key]
    model_keys = pick_critic_models(configured, n)
    if not model_keys:
        log.warning("[Critic] No critic models configured; skipping")
        empty = {"verdict": "approved", "n": 0, "approve_count": 0, "reject_count": 0,
                 "agreement_rate": 0.0, "findings": [], "votes": []}
        queue.set_task_critic_votes(task.id, empty)
        return empty

    client = _get_client()
    cfg = s.models.get("reviewer")
    code = code_to_review or task.coder_output or ""
    messages = build_critic_messages(
        task, code,
        acceptance_summary=acceptance_summary,
        analyst_context=analyst_context,
    )

    votes: list[dict] = []
    for key in model_keys:
        try:
            mm.ensure_loaded_by_key(key) if hasattr(mm, "ensure_loaded_by_key") else None
        except Exception:
            pass
        votes.append(_vote_one_critic(client, key, messages, cfg))

    aggregate = aggregate_critic_votes(votes)
    aggregate["risk"] = task.risk or "med"
    queue.set_task_critic_votes(task.id, aggregate)
    log.info(
        "[Critic] Task #%s quorum verdict=%s (%d/%d approve, agreement=%.0f%%)",
        task.id,
        aggregate["verdict"],
        aggregate["approve_count"],
        aggregate["n"],
        100 * aggregate["agreement_rate"],
    )
    return aggregate


def review_task_with_critics(
    mm: ModelManager,
    queue: TaskQueue,
    task: MicroTask,
    reviewer_verdict: Optional[bool],
    *,
    code_to_review: Optional[str] = None,
    acceptance_summary: Optional[str] = None,
    analyst_context: Optional[str] = None,
) -> Optional[dict]:
    """Run the critic quorum and combine it with the reviewer verdict.

    Behaviour controlled by settings::

        critic_quorum_enabled       — when False, returns None (no-op)
        critic_keep_reviewer_vote   — when True, the reviewer's APPROVED is
                                       only accepted if the critic majority
                                       also approves (i.e. critics override)

    Returns the aggregated payload (also persisted on the task), or None when
    the quorum is disabled.
    """
    s = get_settings()
    if not getattr(s, "critic_quorum_enabled", False):
        return None
    aggregate = critic_quorum_phase(
        mm, queue, task,
        code_to_review=code_to_review,
        acceptance_summary=acceptance_summary,
        analyst_context=analyst_context,
    )
    aggregate["reviewer_verdict"] = (
        "approved" if reviewer_verdict else "rejected"
    ) if reviewer_verdict is not None else None
    return aggregate
