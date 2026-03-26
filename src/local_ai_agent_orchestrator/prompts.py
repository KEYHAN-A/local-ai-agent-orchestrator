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


# ── Message Builders ─────────────────────────────────────────────────


def build_architect_messages(plan_text: str) -> list[dict]:
    """Build messages for the architect phase (plan decomposition)."""
    return [
        {"role": "system", "content": ARCHITECT_SYSTEM},
        {"role": "user", "content": f"Decompose this project plan into micro-tasks:\n\n{plan_text}"},
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
        context_parts.append(
            f"\n## Previous Review Feedback (fix these issues):\n{task.reviewer_feedback}"
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
) -> list[dict]:
    """Build messages for the reviewer phase."""
    content = (
        f"## Task Specification: {task.title}\n\n{task.description}\n\n"
        f"## Code to Review:\n\n{code_output}"
    )

    return [
        {"role": "system", "content": REVIEWER_SYSTEM},
        {"role": "user", "content": content},
    ]
