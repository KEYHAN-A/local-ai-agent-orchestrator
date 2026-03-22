# Contributing

Contributions are welcome under the **GPL-3.0-only** license.

1. Fork and branch from `main`.
2. Run `pip install -e .` and `lao health` against your LM Studio instance.
3. Keep changes focused; update docs when behavior or config changes.
4. By contributing, you agree your contributions are licensed under GPL-3.0-only.

## Code style

- Python 3.10+ typing where helpful.
- Prefer explicit settings via `get_settings()` after `init_settings()`.

## Security

Do not commit `.env`, API keys, or private model paths. The orchestrator can run shell commands—test in isolated workspaces.
