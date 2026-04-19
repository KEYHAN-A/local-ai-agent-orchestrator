---
name: security_audit
description: Lightweight security review of mutating endpoints and shell-touching code.
tools: [file_read, list_dir, shell_exec]
examples:
  - Look for unparameterized SQL, raw shell concatenation, or unbounded reads.
---
Audit checklist:

- Input validation on every external surface (HTTP, CLI, file, env).
- Authentication/authorization on mutating routes.
- No secrets in logs or commit messages.
- Subprocess calls quote arguments and bound the timeout.
- File reads bound the size and refuse paths outside the workspace.
- Crypto uses the platform default with a strong random seed.
