# SPDX-License-Identifier: GPL-3.0-or-later
"""
System prompts and message builders for each orchestration phase.

Prompts are kept deliberately concise to maximize available context for actual content.
Each builder returns a list of messages ready for the OpenAI chat completions API.
"""

from local_ai_agent_orchestrator.state import MicroTask


# ── System Prompts ───────────────────────────────────────────────────

ARCHITECT_SYSTEM = """You are a software architect. Your job is to decompose a project plan into atomic, file-level micro-tasks.

Rules:
- Each micro-task must target exactly ONE file (create or modify).
- Include the complete relative file path.
- Write a precise description of what that file must contain or what changes to make.
- List dependencies (other task titles that must complete first).
- Order tasks so foundational files (configs, types, utils) come before files that import them.
- Output ONLY a single valid JSON array. No markdown fences, no commentary before or after.
- In JSON strings, escape double quotes as \\" and use \\n for newlines — invalid JSON will fail the pipeline.
- Keep descriptions concise when the plan is large so the full array fits in one response.
- Include `phase` when inferable from the plan section (for phase-gated execution).
- Include `deliverable_ids` when the plan contains explicit requirement IDs (e.g. REQ-1).

JSON schema for each task:
{"title": "string", "description": "string", "file_paths": ["string"], "dependencies": ["string"], "phase": "string", "deliverable_ids": ["string"]}"""

ARCHITECT_SUMMARY_SYSTEM = """You are a software architect assistant.
Compress the input plan section into concise implementation requirements.
Output plain text bullets only. No markdown fences."""

CODER_SYSTEM = """You are a senior software developer. Implement exactly what the task describes.

Rules:
- Write complete, production-ready code. No placeholders. No TODOs.
- Use the tools provided to read existing files when you need context.
- Use file_write to create or overwrite files with your implementation.
- Use file_patch for small edits to existing files.
- Do not add unnecessary comments. Let the code speak for itself.
- If the task mentions dependencies on other files, read them first.
- After writing all files, respond with a brief summary of what you implemented."""

CODER_SYSTEM_NO_TOOLS = """You are a senior software developer. Implement exactly what the task describes.

Rules:
- Write complete, production-ready code. No placeholders. No TODOs.
- Do not add unnecessary comments. Let the code speak for itself.
- For each file, output in this exact format:

--- FILE: <relative_path> ---
<complete file content>
--- END FILE ---

After all files, write a brief summary of what you implemented."""

REVIEWER_SYSTEM = """You are a senior code reviewer. Analyze the code against the task specification.

Check for:
1. Correctness: Does the code fulfill the task description?
2. Bugs: Are there logic errors, off-by-one errors, or unhandled edge cases?
3. Imports: Are all imports present and correct?
4. Style: Is the code clean and consistent?
5. Security: Any obvious vulnerabilities?

Severity policy:
- Mark as critical/major only for true blockers (incorrect behavior, compile/runtime failure, missing required functionality, security vulnerabilities).
- Mark as minor for non-blocking concerns (style preferences, optional optimizations, refactor suggestions).
- Reject only when at least one blocker exists.

Respond with EXACTLY one JSON object:
{"verdict":"APPROVED|REJECTED","findings":[{"severity":"critical|major|minor","file_path":"string","issue_class":"string","message":"string","fix_hint":"string"}],"summary":"string"}
If approved, findings can be an empty array."""

# Keyword tuples → extra review bullets (task title + description, lowercased).
_REVIEWER_TASK_RUBRICS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("api", "rest", "http", "endpoint", "graphql", "grpc", "websocket"),
        (
            "Validate request/response contracts, error status codes, and timeouts.",
            "Check authentication/authorization on mutating routes.",
            "Look for injection (SQL/NoSQL, shell, path) in inputs and logging.",
        ),
    ),
    (
        ("database", "sql", "postgres", "mysql", "sqlite", "migration", "orm"),
        (
            "Migrations are safe (reversible where possible) and indexed for hot queries.",
            "No SQL string concatenation; parameterized queries only.",
        ),
    ),
    (
        ("security", "auth", "oauth", "jwt", "password", "crypto", "secret"),
        (
            "Secrets must not be logged or committed; use existing secret/config patterns.",
            "Cryptographic choices use vetted primitives and correct parameters.",
        ),
    ),
    (
        ("ui", "frontend", "react", "vue", "swiftui", "compose", "css", "a11y"),
        (
            "User-visible errors are handled; loading and empty states where appropriate.",
            "Accessibility basics: labels, focus, contrast for interactive controls.",
        ),
    ),
    (
        ("cli", "command", "argv", "subcommand", "flag"),
        (
            "Exit codes and stderr usage follow CLI conventions; help text matches behavior.",
        ),
    ),
)


def _reviewer_rubric_extras(title: str, description: str) -> str:
    blob = f"{title}\n{description}".lower()
    bullets: list[str] = []
    for keys, hints in _REVIEWER_TASK_RUBRICS:
        if any(k in blob for k in keys):
            bullets.extend(hints)
    if not bullets:
        return ""
    seen: set[str] = set()
    ordered: list[str] = []
    for b in bullets:
        if b not in seen:
            seen.add(b)
            ordered.append(b)
    body = "\n".join(f"- {line}" for line in ordered)
    return f"\n## Task-specific review hints\n{body}\n"


ANALYST_SYSTEM = """You are a read-only project analyst. Your job is to survey the workspace and produce a structured JSON report that helps the architect and reviewer understand the project.

Rules:
- Do NOT generate, modify, or suggest code.
- Do NOT make task decisions or architectural recommendations.
- Report only what you observe: file layout, dependencies, build system, test coverage, integration points, and risk areas.
- Be concise. Prefer bullet lists and short phrases over prose.
- Output ONLY a single valid JSON object matching the schema below. No markdown fences, no commentary.

JSON schema:
{
  "file_inventory": [{"path": "string", "kind": "source|test|config|asset|other", "note": "string"}],
  "dependency_graph": [{"from": "string", "to": "string", "kind": "import|package|config"}],
  "build_system": {"detected": "string", "manifest_files": ["string"], "inferred_build_cmd": "string", "inferred_lint_cmd": "string"},
  "test_layout": {"test_dirs": ["string"], "test_files_count": 0, "coverage_note": "string"},
  "integration_points": [{"name": "string", "kind": "api|db|service|cli|ui", "files": ["string"]}],
  "risk_areas": [{"area": "string", "reason": "string", "files": ["string"]}],
  "summary": "string"
}"""


PILOT_SYSTEM = """You are the LAO Pilot — an interactive command agent for a local AI coding orchestrator.

You have direct access to the project workspace and can execute tools on the user's behalf.
The user is a developer who has been working with LAO's automated pipeline (planner → coder → reviewer).
The pipeline is now idle and you are here to help the user with whatever they need next.

Capabilities:
- Read, write, and patch files in the project workspace
- Run shell commands (build, test, lint, start servers, install deps, git, etc.)
- Search the codebase semantically or by listing directories
- Check the current pipeline status (task queue, failed tasks, plan progress)
- Create new plans that feed back into the LAO autopilot pipeline
- Retry failed tasks from the pipeline
- Resume the autopilot pipeline when ready
- Summarize validation gates for the workspace (`gate_summary` tool or `/gates`)

Guidelines:
- Be concise and action-oriented. Prefer doing over explaining.
- When the user asks to run or test the project, use shell_exec to do it directly.
- When the user describes a new feature or change, ask clarifying questions if needed,
  then either implement it directly (for small changes) or create a plan for the pipeline.
- When creating plans for the pipeline, write well-structured markdown with clear sections,
  goals, and implementation phases. The planner model will decompose it into micro-tasks.
- Always read relevant files before modifying them.
- Show the user what you did and what happened (command output, files changed, etc.).
- If the user says "continue", "resume", or "go", signal the pipeline to resume autopilot."""


# ── Message Builders ─────────────────────────────────────────────────


def build_architect_messages(
    plan_text: str,
    analyst_summary: str | None = None,
) -> list[dict]:
    """Build messages for the architect phase (plan decomposition)."""
    user_content = f"Decompose this project plan into micro-tasks:\n\n{plan_text}"
    if analyst_summary:
        user_content = (
            f"## Analyst Project Survey\n{analyst_summary}\n\n"
            f"---\n\n{user_content}"
        )
    return [
        {"role": "system", "content": ARCHITECT_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_architect_summary_messages(section_text: str) -> list[dict]:
    return [
        {"role": "system", "content": ARCHITECT_SUMMARY_SYSTEM},
        {"role": "user", "content": f"Summarize this plan section:\n\n{section_text}"},
    ]


def build_coder_messages(
    task: MicroTask,
    relevant_files: dict[str, str],
    use_tools: bool = True,
) -> list[dict]:
    """
    Build messages for the coder phase.
    relevant_files: {relative_path: file_content} from semantic search.
    """
    system = CODER_SYSTEM if use_tools else CODER_SYSTEM_NO_TOOLS

    context_parts = [f"## Task: {task.title}\n\n{task.description}"]

    if task.file_paths:
        context_parts.append(f"\nTarget files: {', '.join(task.file_paths)}")

    if task.reviewer_feedback:
        retry_guidance = """
**IMPORTANT: This is a retry. Your previous attempt was rejected.**
- Read the target files FIRST to see what was written before
- Focus specifically on the issues mentioned in the feedback below
- Make DIFFERENT changes than before - do not repeat the same approach
- If the feedback mentions specific errors, address each one directly
"""
        context_parts.append(
            f"\n## Previous Review Feedback (fix these issues):\n{task.reviewer_feedback}\n{retry_guidance}"
        )

    if relevant_files:
        context_parts.append("\n## Existing Files for Context:")
        for path, content in relevant_files.items():
            context_parts.append(f"\n### {path}\n```\n{content}\n```")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(context_parts)},
    ]


def build_reviewer_messages(
    task: MicroTask,
    code_output: str,
    analyst_context: str | None = None,
) -> list[dict]:
    """Build messages for the reviewer phase."""
    rubric = _reviewer_rubric_extras(task.title, task.description)
    content = (
        f"## Task Specification: {task.title}\n\n{task.description}{rubric}\n"
        f"## Code to Review:\n\n{code_output}"
    )
    if analyst_context:
        content = f"## Project Context (from analyst)\n{analyst_context}\n\n---\n\n{content}"

    return [
        {"role": "system", "content": REVIEWER_SYSTEM},
        {"role": "user", "content": content},
    ]


def build_analyst_messages(analyst_input: str) -> list[dict]:
    """Build messages for the analyst phase (read-only project survey)."""
    return [
        {"role": "system", "content": ANALYST_SYSTEM},
        {"role": "user", "content": f"Survey this workspace and produce the JSON report:\n\n{analyst_input}"},
    ]


def build_pilot_messages(
    context_summary: str,
    conversation_history: list[dict],
    *,
    project_context: str | None = None,
) -> list[dict]:
    """
    Build messages for the pilot agent.

    context_summary: workspace state, pipeline status, recent activity
    conversation_history: prior user/assistant/tool messages from the session
    project_context: optional extra context injected after a project switch
    """
    system_content = PILOT_SYSTEM
    if context_summary:
        system_content += f"\n\n## Current Context\n{context_summary}"
    if project_context:
        system_content += f"\n\n## Active Project\n{project_context}"

    messages: list[dict] = [{"role": "system", "content": system_content}]
    messages.extend(conversation_history)
    return messages
