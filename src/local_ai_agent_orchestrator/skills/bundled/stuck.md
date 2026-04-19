---
name: stuck
description: Diagnose a stuck loop and propose a different strategy.
tools: [file_read, list_dir, shell_exec]
examples:
  - Read the prior reviewer feedback and identify the literal symbol or path that keeps failing.
---
You appear to be repeating a failing approach. Before another `file_write`:

1. Re-read the target files **and** the previous reviewer feedback.
2. Identify the single root cause keeping the verdict at `rework`.
3. Pick a *different* strategy than your prior attempt -- if you tried a regex
   change, try restructuring; if you tried adding a flag, try removing one.
4. Update the TODO ledger to reflect the new strategy before writing code.
