---
name: verify
description: Mechanical pre-reviewer verification (file exists, parses, TODOs done).
tools: [file_read, list_dir, shell_exec, task_todo_get]
examples:
  - Re-read each target file with file_read and confirm it parses.
  - Run "ls" or "git status" to confirm new files actually exist.
---
Before declaring a coder task done:

1. Call `task_todo_get` and ensure no TODOs are pending or in_progress.
2. For every path you claim to have written, call `file_read` and verify the
   content matches your intent.
3. For Python / JSON / YAML files, parse the body to catch syntax errors.
4. Reply with `Files written: a, b, c` listing only files that actually exist.
