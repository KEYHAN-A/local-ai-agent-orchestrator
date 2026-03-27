# Contributing

Contributions are welcome under the **GPL-3.0-only** license.

1. Fork and branch from `main`.
2. Run `pip install -e .` and verify CLI surfaces:
   - `lao --help`
   - `lao init --help`
   - `lao configure-models --help`
   - `lao health`
3. Run tests: `PYTHONPATH=src python -m unittest discover -s tests -v`
4. Keep changes focused; update README/docs/site content when behavior or config changes. Marketing screenshots for **[docs/index.html](index.html)** and **README** live under **[docs/assets/](assets/)** relative to this folder (keep filenames stable when replacing images).
5. By contributing, you agree your contributions are licensed under GPL-3.0-only.

## Code style

- Python 3.10+ typing where helpful.
- Prefer explicit settings via `get_settings()` after `init_settings()`.
- Keep operator UX coherent across `lao`, `lao init`, `lao configure-models`, and `lao run`.

## Security

Do not commit `.env`, API keys, or private model paths. The orchestrator can run shell commands—test in isolated workspaces.
