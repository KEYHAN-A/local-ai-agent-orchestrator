# Publishing to PyPI

This document is safe to commit. For **tokens and personal notes**, use `PYPI_PUBLISH.local.md` (gitignored).

## One-time setup

1. Create an account at [pypi.org](https://pypi.org/account/register/).
2. Under **Account settings → API tokens**, create a token (entire account scope for the first upload, or per-project after the project exists).
3. Install build tools:
   ```bash
   pip install build twine
   ```

## Check the package name

Confirm `local-ai-agent-orchestrator` is available:  
https://pypi.org/project/local-ai-agent-orchestrator/

## Build

From the repository root:

```bash
python -m build
```

Artifacts appear under `dist/` (`*.tar.gz` and `*.whl`).

## TestPyPI (optional)

1. Register at [test.pypi.org](https://test.pypi.org) and create an API token.
2. Upload:
   ```bash
   python -m twine upload --repository testpypi dist/*
   ```
3. Install:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ local-ai-agent-orchestrator
   ```

## Publish to PyPI

```bash
python -m twine check dist/*
python -m twine upload dist/*
```

Use username `__token__` and password `pypi-...` when prompted, or:

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-xxxxxxxx
python -m twine upload dist/*
```

## After publishing

- Bump `version` in `pyproject.toml` for every new release.
- Users install with: `pip install local-ai-agent-orchestrator` (CLI: `lao`).

## GitHub Actions (optional)

Use [trusted publishing](https://docs.pypi.org/trusted-publishers/) so CI can upload without long-lived tokens on your machine.
