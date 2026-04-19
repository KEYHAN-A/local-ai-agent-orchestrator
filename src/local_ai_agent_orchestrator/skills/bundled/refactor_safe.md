---
name: refactor_safe
description: Refactor without changing observable behavior; keep tests green.
tools: [file_read, file_patch, shell_exec]
---
Refactor protocol:

1. Snapshot current test results with `shell_exec` (capture exit code).
2. Make the smallest mechanical transformation possible.
3. Re-run tests; if they fail, revert with `file_patch` and try a different
   transformation.
4. Never introduce new public API surface during a refactor.
