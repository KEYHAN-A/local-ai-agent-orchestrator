# SPDX-License-Identifier: GPL-3.0-or-later
"""
Orchestration phases: Architect, Coder, Reviewer.

Each phase:
1. Ensures the correct model is loaded via ModelManager
2. Builds messages via prompts module
3. Calls the OpenAI-compatible API on LM Studio
4. Handles tool calls (coder only) or parses structured output (architect)
5. Updates persistent state in SQLite
"""

import json
import time
import re
import logging
from typing import Optional

from openai import OpenAI

from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.prompts import (
    build_architect_messages,
    build_coder_messages,
    build_reviewer_messages,
)
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import MicroTask, TaskQueue
from local_ai_agent_orchestrator.tools import (
    TOOL_DISPATCH,
    TOOL_SCHEMAS,
    file_read,
    find_relevant_files,
)

log = logging.getLogger(__name__)


def _get_client() -> OpenAI:
    s = get_settings()
    return OpenAI(base_url=s.openai_base_url, api_key=s.openai_api_key)


def _llm_call(
    client: OpenAI,
    model_key: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> dict:
    """
    Make an LLM call with retry logic. Returns the full API response dict.
    Handles transient HTTP 500 errors with exponential backoff.
    """
    kwargs = {
        "model": model_key,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": get_settings().llm_request_timeout_s,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    last_error = None
    for attempt in range(get_settings().llm_retry_attempts):
        try:
            response = client.chat.completions.create(**kwargs)
            return response
        except Exception as e:
            last_error = e
            wait = get_settings().llm_retry_backoff_base_s * (2 ** attempt)
            log.warning(
                f"[LLM] Attempt {attempt + 1}/{get_settings().llm_retry_attempts} failed: {e}. "
                f"Retrying in {wait}s..."
            )
            time.sleep(wait)

    raise RuntimeError(
        f"LLM call failed after {get_settings().llm_retry_attempts} attempts: {last_error}"
    )


# ── Phase 1: Architect ───────────────────────────────────────────────


def architect_phase(
    mm: ModelManager,
    queue: TaskQueue,
    plan_id: str,
    plan_text: str,
) -> list[dict]:
    """
    Decompose a master plan into micro-tasks using the Planner model.
    Returns the list of task dicts and inserts them into the queue.
    """
    cfg = get_settings().models["planner"]
    model_key = mm.ensure_loaded("planner")
    client = _get_client()

    messages = build_architect_messages(plan_text)
    start = time.time()

    log.info(f"[Architect] Decomposing plan {plan_id} ({len(plan_text)} chars)")

    try:
        response = _llm_call(
            client, model_key, messages,
            max_tokens=cfg.max_completion,
            temperature=0.3,
        )
        content = response.choices[0].message.content or ""
        duration = time.time() - start
        usage = response.usage

        queue.log_run(
            task_id=None, phase="architect", model_key=model_key,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            duration_seconds=duration, success=True,
        )

        tasks = _parse_architect_output(content)
        if not tasks:
            raise ValueError(f"Architect produced no tasks. Raw output:\n{content[:500]}")

        queue.add_tasks(plan_id, tasks)
        queue.mark_plan_active(plan_id)

        log.info(f"[Architect] Created {len(tasks)} micro-tasks in {duration:.1f}s")
        return tasks

    except Exception as e:
        duration = time.time() - start
        queue.log_run(
            task_id=None, phase="architect", model_key=model_key,
            duration_seconds=duration, success=False, error=str(e),
        )
        raise


def _parse_architect_output(content: str) -> list[dict]:
    """
    Extract JSON array from architect's response.
    Handles: markdown fences, Qwen3 <think>...</think> reasoning blocks,
    and JSON embedded anywhere in a longer text response.
    """
    if not content or not content.strip():
        raise ValueError("Architect returned an empty response")

    # Strip Qwen3 chain-of-thought <think> blocks before parsing
    content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()

    if not content:
        raise ValueError("Architect response was only a <think> block with no JSON output")

    # Strip markdown code fences
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        content = content.strip()

    # Try to find a JSON array anywhere in the response
    match = re.search(r"\[[\s\S]*\]", content)
    if match:
        content = match.group()

    try:
        tasks = json.loads(content)
    except json.JSONDecodeError as e:
        log.error(f"[Architect] JSON parse failed: {e}\nContent: {content[:500]}")
        raise ValueError(f"Failed to parse architect output as JSON: {e}")

    if not isinstance(tasks, list):
        raise ValueError(f"Expected JSON array, got {type(tasks).__name__}")

    validated = []
    for t in tasks:
        validated.append({
            "title": str(t.get("title", "Untitled")),
            "description": str(t.get("description", "")),
            "file_paths": t.get("file_paths", []),
            "dependencies": t.get("dependencies", []),
        })
    return validated


# ── Phase 2: Coder ───────────────────────────────────────────────────


def coder_phase(
    mm: ModelManager,
    queue: TaskQueue,
    task: MicroTask,
) -> str:
    """
    Execute a single micro-task with the Coder model.
    Uses tool calling for file operations. Returns the coder's output summary.
    """
    cfg = get_settings().models["coder"]
    model_key = mm.ensure_loaded("coder")
    client = _get_client()

    queue.mark_coding(task.id)

    # Gather context: semantic search for relevant files
    relevant_files = {}
    try:
        search_query = f"{task.title} {task.description}"
        results = find_relevant_files(search_query, top_k=3)
        for path, _score in results:
            content = file_read(path, max_lines=100)
            if not content.startswith("ERROR"):
                relevant_files[path] = content
    except Exception as e:
        log.warning(f"[Coder] Semantic search failed: {e}")

    messages = build_coder_messages(task, relevant_files, use_tools=cfg.supports_tools)
    start = time.time()
    total_prompt = 0
    total_completion = 0

    log.info(f"[Coder] Task #{task.id}: {task.title} (attempt {task.attempt + 1})")

    try:
        if cfg.supports_tools:
            output = _coder_tool_loop(client, model_key, messages, cfg.max_completion)
        else:
            output = _coder_no_tools(client, model_key, messages, cfg.max_completion)

        duration = time.time() - start
        queue.mark_coded(task.id, output)
        queue.log_run(
            task_id=task.id, phase="coder", model_key=model_key,
            duration_seconds=duration, success=True,
        )

        log.info(f"[Coder] Completed task #{task.id} in {duration:.1f}s")
        return output

    except Exception as e:
        duration = time.time() - start
        queue.log_run(
            task_id=task.id, phase="coder", model_key=model_key,
            duration_seconds=duration, success=False, error=str(e),
        )
        raise


def _coder_tool_loop(
    client: OpenAI,
    model_key: str,
    messages: list[dict],
    max_tokens: int,
    max_rounds: int = 10,
) -> str:
    """
    Run the coder with tool-use in a loop.
    The model calls tools (file_read, file_write, etc.) and we execute them,
    feeding results back until the model produces a final text response.
    """
    files_written = []

    for round_num in range(max_rounds):
        response = _llm_call(
            client, model_key, messages,
            tools=TOOL_SCHEMAS, max_tokens=max_tokens,
        )

        choice = response.choices[0]
        msg = choice.message

        if msg.tool_calls:
            # Add the assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # Execute each tool call
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                log.info(f"[Coder] Tool call: {fn_name}({list(fn_args.keys())})")

                if fn_name in TOOL_DISPATCH:
                    result = TOOL_DISPATCH[fn_name](**fn_args)
                    if fn_name in ("file_write", "file_patch") and result.startswith("OK"):
                        files_written.append(fn_args.get("path", "unknown"))
                else:
                    result = f"ERROR: Unknown tool '{fn_name}'"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)[:3000],
                })

            # Trim message history if it's getting too long (keep system + last N messages)
            if len(messages) > 20:
                messages = [messages[0]] + messages[-16:]

        else:
            # Final text response
            summary = msg.content or "(no summary)"
            if files_written:
                summary += f"\n\nFiles written: {', '.join(files_written)}"
            return summary

    return f"Tool loop ended after {max_rounds} rounds. Files written: {', '.join(files_written)}"


def _coder_no_tools(
    client: OpenAI,
    model_key: str,
    messages: list[dict],
    max_tokens: int,
) -> str:
    """Fallback: coder without tool use. Parses FILE blocks from output."""
    response = _llm_call(client, model_key, messages, max_tokens=max_tokens)
    content = response.choices[0].message.content or ""

    # Parse --- FILE: path --- blocks and write them
    file_blocks = re.findall(
        r"---\s*FILE:\s*(.+?)\s*---\n(.*?)---\s*END FILE\s*---",
        content, re.DOTALL,
    )

    from local_ai_agent_orchestrator.tools import file_write as fw
    files_written = []
    for path, file_content in file_blocks:
        result = fw(path.strip(), file_content)
        if result.startswith("OK"):
            files_written.append(path.strip())

    if files_written:
        return content + f"\n\nFiles written: {', '.join(files_written)}"
    return content


# ── Phase 3: Reviewer ────────────────────────────────────────────────


def reviewer_phase(
    mm: ModelManager,
    queue: TaskQueue,
    task: MicroTask,
) -> bool:
    """
    Review the coder's output. Returns True if approved, False if rejected.
    Updates task state accordingly.
    """
    cfg = get_settings().models["reviewer"]
    model_key = mm.ensure_loaded("reviewer")
    client = _get_client()

    queue.mark_review(task.id)

    # Re-read the task to get latest coder_output
    task = queue.get_task(task.id)
    if not task or not task.coder_output:
        log.error(f"[Reviewer] Task #{task.id} has no coder output to review")
        queue.mark_rework(task.id, "No coder output found")
        return False

    # Also read the actual files written (if mentioned in coder output)
    code_to_review = task.coder_output
    written_files = _extract_written_files(code_to_review)
    for path in written_files[:5]:
        content = file_read(path, max_lines=200)
        if not content.startswith("ERROR"):
            code_to_review += f"\n\n### Actual file: {path}\n```\n{content}\n```"

    messages = build_reviewer_messages(task, code_to_review)
    start = time.time()

    log.info(f"[Reviewer] Reviewing task #{task.id}: {task.title}")

    try:
        response = _llm_call(
            client, model_key, messages,
            max_tokens=cfg.max_completion,
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        duration = time.time() - start
        usage = response.usage

        queue.log_run(
            task_id=task.id, phase="reviewer", model_key=model_key,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            duration_seconds=duration, success=True,
        )

        # Parse verdict
        verdict = content.strip()
        approved = verdict.upper().startswith("APPROVED")

        if approved:
            queue.mark_completed(task.id)
            log.info(f"[Reviewer] APPROVED task #{task.id} in {duration:.1f}s")
        else:
            feedback = verdict
            if task.attempt + 1 >= task.max_attempts:
                queue.mark_failed(task.id, f"Max attempts reached. Last feedback: {feedback}")
                log.warning(f"[Reviewer] Task #{task.id} FAILED after {task.max_attempts} attempts")
            else:
                queue.mark_rework(task.id, feedback)
                log.info(f"[Reviewer] REJECTED task #{task.id}: {feedback[:100]}...")

        return approved

    except Exception as e:
        duration = time.time() - start
        queue.log_run(
            task_id=task.id, phase="reviewer", model_key=model_key,
            duration_seconds=duration, success=False, error=str(e),
        )
        raise


def _extract_written_files(coder_output: str) -> list[str]:
    """Pull file paths from 'Files written: x, y, z' in coder output."""
    match = re.search(r"Files written:\s*(.+)", coder_output)
    if match:
        return [f.strip() for f in match.group(1).split(",") if f.strip()]
    return []
