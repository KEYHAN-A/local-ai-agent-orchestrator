---
name: repro_bug
description: Produce a minimal failing reproduction before attempting a fix.
tools: [file_read, file_write, shell_exec]
---
Reproduction protocol:

1. Identify the smallest input or call that triggers the bug.
2. Add a failing test (skip if it already exists). Confirm it fails locally
   with `shell_exec`.
3. Only then start the fix. The same test must pass after your changes.
4. Quote the failing output verbatim in your task summary.
