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

- Bump `version` in `pyproject.toml` and `__version__` in `src/local_ai_agent_orchestrator/__init__.py` for every new release.
- Add release notes under **[CHANGELOG.md](../CHANGELOG.md)** (canonical history).
- Users install with: `pip install local-ai-agent-orchestrator` (CLI: `lao`).

## Release checklist (maintainers)

1. Bump version in `pyproject.toml` and `src/local_ai_agent_orchestrator/__init__.py`.
2. Update **[CHANGELOG.md](../CHANGELOG.md)** and, if needed, **[README.md](../README.md)** / **[docs/index.html](index.html)** (GitHub Pages source).
3. Run tests: `python -m unittest discover -s tests -v`
4. Commit and push `main`.
5. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
6. **Create a GitHub Release** for that tag (UI or `gh release create vX.Y.Z --notes-file …`). This is what triggers automated PyPI upload in CI.
7. Confirm the **Publish to PyPI** workflow succeeded on the `pypi` environment.
8. Manual fallback: `python -m build`, `python -m twine check dist/*`, `python -m twine upload dist/*` (token auth), or run the workflow via **Actions → Publish to PyPI → Run workflow** (`workflow_dispatch`).

## GitHub Actions (recommended)

Use [trusted publishing](https://docs.pypi.org/trusted-publishers/) so CI can upload without long-lived tokens on your machine.

This repository includes [`.github/workflows/publish-pypi.yml`](../.github/workflows/publish-pypi.yml), which publishes when:

- A **GitHub Release** is **published** (`release: types: [published]`), or
- The workflow is run manually (**workflow_dispatch**).

Pushing a `v*` tag alone **does not** trigger this workflow; create the **Release** (or dispatch the workflow) after the tag exists.

### Configure Trusted Publisher once

1. In PyPI project settings, add a Trusted Publisher:
   - Owner: `KEYHAN-A`
   - Repository: `local-ai-agent-orchestrator`
   - Workflow: `publish-pypi.yml`
   - Environment: `pypi`
2. In GitHub, create environment `pypi` (optional protections as desired).

After this, **publishing a GitHub Release** (or a manual workflow run) uploads to PyPI automatically.
