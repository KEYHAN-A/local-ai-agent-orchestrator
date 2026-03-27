# SPDX-License-Identifier: GPL-3.0-or-later
"""
Workspace tools for the coding agents.

Plain Python functions (no framework overhead). These are called directly by the
phases module and can also be wired into OpenAI function-calling tool schemas.
"""

import os
import subprocess
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from local_ai_agent_orchestrator.settings import get_settings

if TYPE_CHECKING:
    from local_ai_agent_orchestrator.state import TaskQueue

log = logging.getLogger(__name__)

_ACTIVE_WORKSPACE: ContextVar[Optional[Path]] = ContextVar("lao_active_workspace", default=None)

# ── File Operations ──────────────────────────────────────────────────


def _workspace_root() -> Path:
    w = _ACTIVE_WORKSPACE.get()
    if w is not None:
        return w.resolve()
    return get_settings().workspace_root.resolve()


@contextmanager
def use_plan_workspace(queue: "TaskQueue", plan_id: str) -> Iterator[Path]:
    """Set the active workspace to this plan's `<config_dir>/<stem>/` for the block."""
    path = queue.workspace_for_plan(plan_id)
    token = _ACTIVE_WORKSPACE.set(path)
    try:
        yield path
    finally:
        _ACTIVE_WORKSPACE.reset(token)


def file_read(path: str, max_lines: int = 500) -> str:
    """Read a file from the workspace. Returns content or error string."""
    full = _resolve_path(path)
    if not full:
        return f"ERROR: Path '{path}' is outside the workspace."
    if not full.exists():
        return f"ERROR: File not found: {path}"
    if not full.is_file():
        return f"ERROR: Not a file: {path}"
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n\n... truncated ({len(lines)} total lines)"
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


def file_write(path: str, content: str) -> str:
    """Write content to a file in the workspace. Creates directories as needed."""
    full = _resolve_path(path)
    if not full:
        return f"ERROR: Path '{path}' is outside the workspace."
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        log.info(f"[Tools] Wrote {len(content)} chars to {path}")
        return f"OK: Written to {path}"
    except Exception as e:
        return f"ERROR: {e}"


def file_patch(path: str, old: str, new: str) -> str:
    """Replace `old` with `new` in an existing file. For surgical edits."""
    full = _resolve_path(path)
    if not full:
        return f"ERROR: Path '{path}' is outside the workspace."
    if not full.exists():
        return f"ERROR: File not found: {path}"
    try:
        content = full.read_text(encoding="utf-8")
        if old not in content:
            return f"ERROR: The old string was not found in {path}"
        content = content.replace(old, new, 1)
        full.write_text(content, encoding="utf-8")
        return f"OK: Patched {path}"
    except Exception as e:
        return f"ERROR: {e}"


# ── Directory Operations ─────────────────────────────────────────────


def list_dir(path: str = ".", max_depth: int = 3) -> str:
    """List directory contents. Returns a tree-style listing."""
    full = _resolve_path(path)
    if not full:
        return f"ERROR: Path '{path}' is outside the workspace."
    if not full.exists():
        return f"ERROR: Directory not found: {path}"
    if not full.is_dir():
        return f"ERROR: Not a directory: {path}"

    lines = []
    _walk_tree(full, full, lines, max_depth, 0)
    if not lines:
        return "(empty directory)"
    return "\n".join(lines)


def _walk_tree(root: Path, current: Path, lines: list, max_depth: int, depth: int):
    if depth > max_depth:
        return
    try:
        entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
    except PermissionError:
        return
    for entry in entries:
        if entry.name.startswith("."):
            continue
        rel = entry.relative_to(root)
        prefix = "  " * depth
        if entry.is_dir():
            lines.append(f"{prefix}{rel}/")
            _walk_tree(root, entry, lines, max_depth, depth + 1)
        else:
            size = entry.stat().st_size
            lines.append(f"{prefix}{rel}  ({_human_size(size)})")


# ── Shell Execution ──────────────────────────────────────────────────


def shell_exec(command: str, timeout: int = 60, cwd: Optional[str] = None) -> str:
    """
    Execute a shell command within the workspace.
    Returns combined stdout+stderr, capped at 4000 chars.
    """
    wr = _workspace_root()
    work_dir = _resolve_path(cwd) if cwd else wr
    if not work_dir or not work_dir.is_dir():
        work_dir = wr

    # Block dangerous commands
    blocked = ["rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:"]
    for b in blocked:
        if b in command:
            return f"ERROR: Blocked dangerous command pattern: {b}"

    log.info(f"[Tools] Shell: {command} (cwd={work_dir})")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n--- stderr ---\n" + result.stderr) if output else result.stderr
        if not output:
            output = "(no output)"
        output = f"exit_code: {result.returncode}\n{output}"
        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"
        return output
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


# ── Semantic Search (Embedding-powered file retrieval) ───────────────


def find_relevant_files(
    query: str,
    workspace_path: str = ".",
    top_k: int = 5,
) -> list[tuple[str, float]]:
    """
    Use Nomic Embed to find files most relevant to `query`.
    Returns list of (relative_path, similarity_score) tuples.
    Falls back to keyword matching if embedding model is unavailable.
    """
    ws = _resolve_path(workspace_path) or _workspace_root()

    # Collect indexable files
    files_with_content = []
    for ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
                ".json", ".yaml", ".yml", ".md", ".txt", ".toml", ".cfg",
                ".sh", ".sql", ".go", ".rs", ".java", ".c", ".cpp", ".h"):
        for f in ws.rglob(f"*{ext}"):
            if any(skip in f.parts for skip in ("node_modules", ".git", "__pycache__", ".venv")):
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
        log.warning(f"[Tools] Embedding search failed ({e}), falling back to keyword match")
        return _keyword_search(query, files_with_content, top_k)


def _embed_search(
    query: str,
    files: list[tuple[str, str]],
    top_k: int,
) -> list[tuple[str, float]]:
    """Compute cosine similarity between query embedding and file content embeddings."""
    import requests as req

    s = get_settings()
    base = s.lm_studio_base.rstrip("/")
    embed_url = f"{base}/v1/embeddings"
    model = s.models["embedder"].key

    # Embed the query
    r = req.post(embed_url, json={
        "model": model,
        "input": f"search_query: {query}",
    }, timeout=30, headers={"Authorization": f"Bearer {s.openai_api_key}"})
    r.raise_for_status()
    q_vec = r.json()["data"][0]["embedding"]

    # Embed file contents in batches
    batch_size = 10
    all_scores = []
    for i in range(0, len(files), batch_size):
        batch = files[i:i + batch_size]
        inputs = [f"search_document: {name}\n{content[:500]}" for name, content in batch]
        r = req.post(embed_url, json={
            "model": model,
            "input": inputs,
        }, timeout=60, headers={"Authorization": f"Bearer {s.openai_api_key}"})
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
    """Simple keyword matching fallback."""
    keywords = set(query.lower().split())
    scored = []
    for name, content in files:
        combined = (name + " " + content).lower()
        hits = sum(1 for kw in keywords if kw in combined)
        if hits > 0:
            scored.append((name, hits / len(keywords)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── OpenAI Function-Calling Tool Schemas ─────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read a file from the project workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from workspace root"},
                    "max_lines": {"type": "integer", "description": "Max lines to return (default 500)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file (creates dirs as needed)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from workspace root"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_patch",
            "description": "Replace a specific string in an existing file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from workspace root"},
                    "old": {"type": "string", "description": "Exact string to find"},
                    "new": {"type": "string", "description": "Replacement string"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents with tree structure",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path (default: workspace root)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Run a shell command in the workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"},
                },
                "required": ["command"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "file_read": file_read,
    "file_write": file_write,
    "file_patch": file_patch,
    "list_dir": list_dir,
    "shell_exec": shell_exec,
}


# ── Internal Helpers ─────────────────────────────────────────────────


_ALLOWED_PROJECT: ContextVar[Optional[Path]] = ContextVar("lao_allowed_project", default=None)


@contextmanager
def allow_project_access(project_path: Path) -> Iterator[None]:
    """Temporarily allow tool access to *project_path* in addition to the workspace root."""
    token = _ALLOWED_PROJECT.set(project_path.resolve())
    try:
        yield
    finally:
        _ALLOWED_PROJECT.reset(token)


def _resolve_path(path: str) -> Optional[Path]:
    """Resolve a path relative to workspace root, ensuring it stays within bounds.

    When an explicit project has been allowed via :func:`allow_project_access`,
    paths inside that project are also accepted.
    """
    root = _workspace_root().resolve()
    if path is None:
        return root
    p = Path(path)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (root / p).resolve()

    roots = [root]
    allowed = _ALLOWED_PROJECT.get()
    if allowed is not None:
        roots.append(allowed)

    for candidate_root in roots:
        try:
            resolved.relative_to(candidate_root)
            return resolved
        except ValueError:
            continue
    return None


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.0f}{unit}"
        nbytes /= 1024
    return f"{nbytes:.1f}TB"
