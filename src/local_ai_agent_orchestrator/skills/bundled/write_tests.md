---
name: write_tests
description: Add focused tests for new behavior; one test per branch / public symbol.
tools: [file_read, file_write, shell_exec]
---
When adding tests:

1. Place files under the project's existing test root (auto-detected from
   `pyproject.toml`, `package.json`, etc.).
2. One test per externally observable behavior (happy path, edge case, error).
3. Avoid mocking pure functions; mock only true I/O (network, disk side
   effects, time).
4. After writing, attempt to run the tests with `shell_exec` and capture the
   exit code in your final summary.
