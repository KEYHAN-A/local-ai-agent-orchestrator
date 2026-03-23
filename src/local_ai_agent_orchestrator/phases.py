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
import logging
import re
import time
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator import plan_git

from openai import OpenAI

from local_ai_agent_orchestrator.model_manager import ModelManager
from local_ai_agent_orchestrator.prompts import (
    build_architect_messages,
    build_architect_summary_messages,
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
from local_ai_agent_orchestrator.validators import (
    extract_written_files,
    validate_files,
    validate_reviewer_json,
)

log = logging.getLogger(__name__)

# Strip model chain-of-thought (e.g. Qwen3 / DeepSeek-R1 distill) before parsing.
_THINKING_BLOCK_RES = (
    re.compile(r"\x3cthink\x3e[\s\S]*?\x3c/think\x3e", re.IGNORECASE),
)


def _strip_thinking_blocks(text: str) -> str:
    """Remove chain-of-thought wrappers so verdict / JSON parsers see the answer."""
    for pat in _THINKING_BLOCK_RES:
        text = pat.sub("", text)
    return text.strip()


def _estimate_chat_prompt_tokens(messages: list[dict]) -> int:
    """
    Approximate token count for architect/coder-style chat messages.
    Uses cl100k_base when tiktoken is available; otherwise a char heuristic.
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for m in messages:
            body = m.get("content") or ""
            total += 4 + len(enc.encode(body))
        return total
    except Exception:
        return sum(len((m.get("content") or "")) // 3 + 4 for m in messages)


def _extract_first_json_array(text: str) -> str | None:
    """
    Find the first top-level JSON array by bracket depth, respecting strings.
    Avoids greedy ``[...]`` regex that can span past the real array end.
    """
    start = text.find("[")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return None


def _architect_max_tokens(planner_cfg, messages: list[dict]) -> int:
    """
    Cap completion tokens so prompt + completion fits in context_length.
    """
    prompt_est = _estimate_chat_prompt_tokens(messages)
    ctx = planner_cfg.context_length
    utilization = get_settings().max_context_utilization
    target_ctx = int(ctx * utilization)
    reserved = 256
    headroom = target_ctx - prompt_est - reserved
    if headroom < 1024:
        raise ValueError(
            f"Plan is too large for the planner model context: context_length={ctx}, "
            f"estimated prompt tokens ~{prompt_est}. Raise models.planner.context_length "
            "in factory.yaml (e.g. 65536) or split the plan into smaller markdown files."
        )
    max_out = min(planner_cfg.max_completion, headroom)
    if max_out < planner_cfg.max_completion:
        log.warning(
            "[Architect] max_tokens=%s (capped from max_completion=%s; context=%s, ~prompt_tokens=%s)",
            max_out,
            planner_cfg.max_completion,
            ctx,
            prompt_est,
        )
    return max_out


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
    plan_filename: str,
) -> list[dict]:
    """
    Decompose a master plan into micro-tasks using the Planner model.
    Returns the list of task dicts and inserts them into the queue.
    """
    cfg = get_settings().models["planner"]
    model_key = mm.ensure_loaded("planner")
    client = _get_client()

    chunks = _chunk_plan_for_architect(plan_text, cfg.context_length)
    for idx, chunk in enumerate(chunks):
        queue.upsert_plan_chunk(plan_id, idx, chunk)
    start = time.time()

    log.info(f"[Architect] Decomposing plan {plan_id} ({len(plan_text)} chars)")

    try:
        all_tasks: list[dict] = []
        for idx, chunk in enumerate(chunks):
            existing = queue.get_plan_chunks(plan_id)
            row = next((c for c in existing if c["chunk_index"] == idx), None)
            if row and row["status"] == "completed" and row.get("tasks"):
                all_tasks.extend(row["tasks"])
                continue

            messages = build_architect_messages(chunk)
            try:
                max_out = _architect_max_tokens(cfg, messages)
            except ValueError:
                summary_messages = build_architect_summary_messages(chunk)
                summary_response = _llm_call(
                    client, model_key, summary_messages, max_tokens=1024, temperature=0.2
                )
                summary = summary_response.choices[0].message.content or chunk[:4000]
                messages = build_architect_messages(summary)
                max_out = _architect_max_tokens(cfg, messages)

            response = _llm_call(
                client, model_key, messages, max_tokens=max_out, temperature=0.3
            )
            choice = response.choices[0]
            content = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", None)
            usage = response.usage
            queue.log_run(
                task_id=None,
                phase="architect",
                model_key=model_key,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                duration_seconds=0.0,
                success=True,
            )
            try:
                tasks = _parse_architect_output(content)
            except ValueError as ve:
                queue.mark_plan_chunk_failed(plan_id, idx, str(ve))
                if finish_reason == "length":
                    raise ValueError(
                        "Architect hit output token limit. Increase planner context/max completion, "
                        "or split plan further."
                    ) from ve
                raise
            if not tasks:
                queue.mark_plan_chunk_failed(plan_id, idx, "No tasks generated")
                raise ValueError(f"Architect produced no tasks for chunk {idx}.")
            queue.mark_plan_chunk_done(plan_id, idx, tasks)
            all_tasks.extend(tasks)

        queue.add_tasks(plan_id, all_tasks)
        queue.mark_plan_active(plan_id)

        duration = time.time() - start
        log.info(f"[Architect] Created {len(all_tasks)} micro-tasks in {duration:.1f}s")
        plan_git.commit_after_architect(
            queue.workspace_for_plan(plan_id),
            queue,
            plan_id,
            Path(plan_filename).stem,
            len(all_tasks),
        )
        return all_tasks

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

    # Strip chain-of-thought blocks before parsing
    content = _strip_thinking_blocks(content)

    if not content:
        raise ValueError("Architect response was only chain-of-thought with no JSON output")

    # Strip markdown code fences
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        content = content.strip()

    candidates: list[str] = []
    extracted = _extract_first_json_array(content)
    if extracted:
        candidates.append(extracted)
    stripped = content.strip()
    if stripped not in candidates:
        candidates.append(stripped)

    last_err: json.JSONDecodeError | None = None
    tasks = None
    for cand in candidates:
        try:
            tasks = json.loads(cand)
            last_err = None
            break
        except json.JSONDecodeError as e:
            last_err = e
            continue

    if last_err is not None or tasks is None:
        assert last_err is not None
        preview = (candidates[0] if candidates else content)[:800]
        log.error(f"[Architect] JSON parse failed: {last_err}\nContent (preview): {preview}")
        raise ValueError(f"Failed to parse architect output as JSON: {last_err}")

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
        plan_git.commit_after_coder(
            queue.workspace_for_plan(task.plan_id),
            task.plan_id,
            task.id,
            task.title,
        )
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


def _chunk_plan_for_architect(plan_text: str, context_length: int) -> list[str]:
    # Conservative char-based chunking to avoid context failures.
    max_chars = max(3000, int(context_length * 2.2))
    if len(plan_text) <= max_chars:
        return [plan_text]
    sections = re.split(r"\n(?=#|\-\s|\d+\.)", plan_text)
    chunks: list[str] = []
    cur = ""
    for s in sections:
        if len(cur) + len(s) + 1 <= max_chars:
            cur = f"{cur}\n{s}".strip()
            continue
        if cur:
            chunks.append(cur)
        cur = s
    if cur:
        chunks.append(cur)
    return chunks


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
        tid = task.id if task else None
        log.error(f"[Reviewer] Task #{tid} has no coder output to review")
        if task:
            queue.mark_rework(task.id, "No coder output found")
            plan_git.commit_after_reviewer(
                queue.workspace_for_plan(task.plan_id),
                task.plan_id,
                task.id,
                task.title,
                "rejected",
            )
        return False

    # Also read the actual files written (if mentioned in coder output)
    code_to_review = task.coder_output
    written_files = extract_written_files(code_to_review)
    queue.clear_findings(task.id)
    validation_findings = validate_files(queue.workspace_for_plan(task.plan_id), written_files[:20])
    if validation_findings:
        for f in validation_findings:
            queue.add_finding(
                task.id,
                source="validator",
                severity=f.severity,
                issue_class=f.issue_class,
                message=f.message,
                file_path=f.file_path,
                fix_hint=f.fix_hint,
            )
        if get_settings().quality_gate_mode in ("standard", "strict"):
            feedback = "\n".join(
                f"- [{f.severity}] {f.issue_class}: {f.message}" for f in validation_findings
            )
            queue.mark_rework(task.id, f"Validation gate failed:\n{feedback}")
            log.info(f"[Reviewer] Validation gate rejected task #{task.id}")
            return False
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

        approved, reviewer_findings, summary = validate_reviewer_json(_strip_thinking_blocks(content))
        for f in reviewer_findings:
            queue.add_finding(
                task.id,
                source="reviewer",
                severity=f.severity,
                issue_class=f.issue_class,
                message=f.message,
                file_path=f.file_path,
                fix_hint=f.fix_hint,
            )
        feedback = summary or content

        ws = queue.workspace_for_plan(task.plan_id)
        if approved:
            queue.mark_completed(task.id)
            log.info(f"[Reviewer] APPROVED task #{task.id} in {duration:.1f}s")
            plan_git.commit_after_reviewer(
                ws, task.plan_id, task.id, task.title, "approved"
            )
        else:
            if task.attempt + 1 >= task.max_attempts:
                queue.mark_failed(task.id, f"Max attempts reached. Last feedback: {feedback}")
                log.warning(f"[Reviewer] Task #{task.id} FAILED after {task.max_attempts} attempts")
                plan_git.commit_after_reviewer(
                    ws, task.plan_id, task.id, task.title, "failed"
                )
            else:
                structured = "\n".join(
                    f"- [{f.severity}] {f.file_path or '-'} {f.issue_class}: {f.message}"
                    for f in reviewer_findings
                )
                queue.mark_rework(task.id, structured or feedback)
                log.info(f"[Reviewer] REJECTED task #{task.id}: {feedback[:100]}...")
                plan_git.commit_after_reviewer(
                    ws, task.plan_id, task.id, task.title, "rejected"
                )

        return approved

    except Exception as e:
        duration = time.time() - start
        queue.log_run(
            task_id=task.id, phase="reviewer", model_key=model_key,
            duration_seconds=duration, success=False, error=str(e),
        )
        raise


