# SPDX-License-Identifier: GPL-3.0-or-later
"""
Conversation compaction.

Replaces the historical naive ``[messages[0]] + messages[-16:]`` trim with a
context-aware compactor that:

1. Always keeps the system prompt at index 0.
2. Always keeps the last *N* turns (default ``settings.compaction_keep_recent``).
3. Optionally summarizes the dropped middle turns into a single
   ``assistant`` message via the cheapest available role model.

When the LLM summarizer is unavailable (e.g. during tests or when the
embedder/analyst model is offline) the function falls back to a deterministic
character-budget summary so callers never raise.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from local_ai_agent_orchestrator.settings import get_settings

log = logging.getLogger(__name__)


_SUMMARY_PROMPT = (
    "Summarize the following conversation excerpt into 5-10 short bullet points "
    "capturing decisions made, files written, errors observed, and outstanding "
    "TODOs. Keep file paths verbatim. Output as plain text bullets."
)


def _char_budget_summary(messages: list[dict], char_budget: int = 1500) -> str:
    """Cheap deterministic fallback when no summarizer is available."""
    chunks: list[str] = []
    used = 0
    for m in messages:
        body = (m.get("content") or "").strip()
        if not body:
            continue
        head = body if len(body) <= 200 else body[:200] + "..."
        line = f"- [{m.get('role','?')}] {head}"
        if used + len(line) > char_budget:
            break
        chunks.append(line)
        used += len(line)
    return "Earlier turns summary (auto-truncated):\n" + "\n".join(chunks)


def compact_messages(
    messages: list[dict],
    *,
    keep_recent: Optional[int] = None,
    threshold: int = 20,
    summarizer: Optional[Callable[[list[dict]], str]] = None,
) -> list[dict]:
    """Return a compacted copy of *messages* if larger than *threshold*.

    - ``messages[0]`` (system) is always preserved.
    - The last ``keep_recent`` messages are preserved verbatim.
    - The middle is replaced by a single synthetic ``assistant`` summary.
    """
    if not messages:
        return list(messages)
    try:
        s = get_settings()
        if not s.compaction_enabled:
            return list(messages)
        if keep_recent is None:
            keep_recent = max(1, int(s.compaction_keep_recent))
    except RuntimeError:
        if keep_recent is None:
            keep_recent = 8

    if len(messages) <= max(threshold, keep_recent + 2):
        return list(messages)

    head = messages[0]
    tail = messages[-keep_recent:]
    middle = messages[1:-keep_recent] if keep_recent < len(messages) - 1 else []

    if not middle:
        return [head, *tail]

    if summarizer is not None:
        try:
            summary_text = summarizer(middle)
        except Exception as e:
            log.warning(f"[Compact] summarizer raised: {e}; using char-budget fallback")
            summary_text = _char_budget_summary(middle)
    else:
        summary_text = _char_budget_summary(middle)

    if not summary_text.startswith("Earlier turns summary"):
        summary_text = "Earlier turns summary:\n" + summary_text

    summary_msg = {"role": "assistant", "content": summary_text}
    return [head, summary_msg, *tail]


def make_llm_summarizer(client, model_key: str, *, max_tokens: int = 512) -> Callable[[list[dict]], str]:
    """Construct a summarizer that uses the cheapest available chat model."""
    from local_ai_agent_orchestrator.phases import _llm_call  # local import to avoid cycle

    def _summarize(middle: list[dict]) -> str:
        snippet = []
        for m in middle:
            body = (m.get("content") or "")
            if len(body) > 1500:
                body = body[:1500] + "..."
            snippet.append(f"[{m.get('role','?')}] {body}")
        joined = "\n\n".join(snippet)
        msgs = [
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": joined[:8000]},
        ]
        resp = _llm_call(client, model_key, msgs, max_tokens=max_tokens, temperature=0.0)
        try:
            return resp.choices[0].message.content or _char_budget_summary(middle)
        except Exception:
            return _char_budget_summary(middle)

    return _summarize
