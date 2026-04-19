# SPDX-License-Identifier: GPL-3.0-or-later
"""Embedding-powered semantic file search with a keyword fallback."""

from __future__ import annotations

import logging

from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.tools.base import (
    Tool,
    param,
    parameters_schema,
    register,
)
from local_ai_agent_orchestrator.tools.meta import resolve_path, tools_workspace_root

log = logging.getLogger(__name__)


_INDEXABLE_EXTENSIONS = (
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".md", ".txt", ".toml", ".cfg",
    ".sh", ".sql", ".go", ".rs", ".java", ".c", ".cpp", ".h",
)
_SKIP_PARTS = ("node_modules", ".git", "__pycache__", ".venv")


def find_relevant_files(
    query: str,
    workspace_path: str = ".",
    top_k: int = 5,
) -> list[tuple[str, float]]:
    """Rank workspace files against *query* using embeddings, falling back to keywords."""
    ws = resolve_path(workspace_path) or tools_workspace_root()
    files_with_content: list[tuple[str, str]] = []
    for ext in _INDEXABLE_EXTENSIONS:
        for f in ws.rglob(f"*{ext}"):
            if any(skip in f.parts for skip in _SKIP_PARTS):
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[:2000]
                rel = str(f.relative_to(ws))
                files_with_content.append((rel, content))
            except Exception:
                continue
    if not files_with_content:
        return []
    try:
        return _embed_search(query, files_with_content, top_k)
    except Exception as e:
        log.warning(f"[Tools] Embedding search failed ({e}); falling back to keyword match")
        return _keyword_search(query, files_with_content, top_k)


def _embed_search(
    query: str,
    files: list[tuple[str, str]],
    top_k: int,
) -> list[tuple[str, float]]:
    import requests

    s = get_settings()
    base = s.lm_studio_base.rstrip("/")
    embed_url = f"{base}/v1/embeddings"
    model = s.models["embedder"].key

    r = requests.post(
        embed_url,
        json={"model": model, "input": f"search_query: {query}"},
        timeout=30,
        headers={"Authorization": f"Bearer {s.openai_api_key}"},
    )
    r.raise_for_status()
    q_vec = r.json()["data"][0]["embedding"]

    batch_size = 10
    all_scores: list[tuple[str, float]] = []
    for i in range(0, len(files), batch_size):
        batch = files[i : i + batch_size]
        inputs = [f"search_document: {name}\n{content[:500]}" for name, content in batch]
        r = requests.post(
            embed_url,
            json={"model": model, "input": inputs},
            timeout=60,
            headers={"Authorization": f"Bearer {s.openai_api_key}"},
        )
        r.raise_for_status()
        for j, emb_data in enumerate(r.json()["data"]):
            score = _cosine_sim(q_vec, emb_data["embedding"])
            all_scores.append((batch[j][0], score))

    all_scores.sort(key=lambda x: x[1], reverse=True)
    return all_scores[:top_k]


def _keyword_search(
    query: str,
    files: list[tuple[str, str]],
    top_k: int,
) -> list[tuple[str, float]]:
    keywords = set(query.lower().split())
    scored: list[tuple[str, float]] = []
    for name, content in files:
        combined = (name + " " + content).lower()
        hits = sum(1 for kw in keywords if kw in combined)
        if hits > 0:
            scored.append((name, hits / max(1, len(keywords))))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


FIND_RELEVANT_FILES_TOOL = register(
    Tool(
        name="find_relevant_files",
        description=(
            "Rank workspace files by semantic relevance to the query. Falls back to "
            "keyword matching when the embedder model is unavailable."
        ),
        parameters=parameters_schema(
            {
                "query": param("string", "Natural-language query."),
                "workspace_path": param(
                    "string",
                    "Workspace-relative root to search (defaults to '.').",
                    default=".",
                ),
                "top_k": param("integer", "Maximum number of files to return.", default=5),
            },
            required=["query"],
        ),
        call=find_relevant_files,
        is_read_only=True,
        is_concurrency_safe=True,
        plan_mode_safe=True,
        prompt_contribution=(
            "find_relevant_files(query, workspace_path='.', top_k=5) ranks files by relevance."
        ),
    )
)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
