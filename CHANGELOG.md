# Changelog

All notable changes to **Local AI Agent Orchestrator** are recorded here. For install and usage, see [README.md](README.md).

## v3.0.7 — README hero screenshot & home menu asset

- **README:** Home menu image moved to the top (under the title); duplicate screenshot removed from the Pilot section; refreshed **`docs/assets/lao-home-menu.png`** for PyPI and GitHub rendering.

## v3.0.6 — README & site: installation up front

- **README:** **Installation** is first in the table of contents and appears immediately after the TOC (before **Features**).
- **Site:** Meta description and hero copy foreground install paths (`pip` + link to the curl installer); housekeeping version strings to **v3.0.6**.

## v3.0.5 — Install scripting & distribution docs

- **Install:** `scripts/install.sh` (prefers **pipx**, else `pip install --user`) and optional env `LAO_VERSION` / `LAO_PACKAGE`.
- **Site:** `docs/install.sh` bootstrap for [lao.keyhan.info/install.sh](https://lao.keyhan.info/install.sh) (delegates to the canonical script on GitHub).
- **Docs:** README guidance for curl-based install (trust trade-offs), Homebrew via **pipx**, and why npm is not used for this Python CLI.

## v3.0.4 — LAO Pilot Mode

This release highlights **Pilot Mode**: an interactive local-LLM chat that runs workspace tools, inspects pipeline status, creates plans, and hands control back to the autopilot pipeline when you are ready.

- **Pilot Mode:** Full-screen unified CLI (`UnifiedUI` + prompt_toolkit) for continuous scrollback, status bar, and robust terminal rendering (banner outside `patch_stdout` to avoid ANSI mangling).
- **Project-aware Pilot:** `ProjectRegistry` (`~/.lao/projects.json`), `lao projects` subcommands, `/project` in chat, intent-based project resolution, tool-loop bailout after repeated errors, and clearer empty-LLM fallbacks.
- **Slash vs path:** Absolute paths (e.g. `/Users/...`) are no longer mistaken for slash commands.
- **Home menu UX:** Primary actions grouped (Initialize workspace + Pilot), then other actions with `Exit` last; default focus on **Initialize workspace** (home-root safety still defaults to Exit).
- **Branding:** Updated block-style **LAO** ASCII logo on splash and pilot banner.
- **Input:** Double **Ctrl+C** exits pilot prompt; single Ctrl+C nudges to press again or keep typing.
- **Docs & site:** README and [lao.keyhan.info](https://lao.keyhan.info) (`docs/index.html`) updated with Pilot workflow, terminal screenshots under `docs/assets/`, and expanded architecture/configuration notes.

## v2.3.0

- **Release rollout:** publish preparation for `v2.3.0` with project metadata version bump and release packaging flow.
- **Orchestrator baseline:** ships current LAO pipeline state as the next tagged version for distribution.

## v2.2.1

- **Reviewer parsing robustness:** reviewer JSON validation now accepts markdown-fenced JSON and mixed-text wrappers, preventing false task rejection when verdict payloads are wrapped in JSON code fences.
- **Regression coverage:** added validator tests for fenced reviewer responses so `APPROVED` verdicts are correctly recognized.

## v2.2.0

- **Pipeline completion correctness:** plans now move to `completed` when all tasks reach a terminal state (`completed` or `failed`) instead of staying `active`.
- **Recovery ergonomics:** added `lao retry-failed` (with `reset-failed` alias) backed by queue API support to quickly re-run failed tasks.
- **Scheduler robustness:** pending tasks that depend on failed prerequisites are now auto-failed with explicit dependency-block feedback.
- **Reviewer quality gate tuning:** reviewer guidance and parsing now treat minor-only feedback as non-blocking, reducing over-rejection loops on local models.
- **Test coverage:** added tests for terminal-plan detection, failed-task reset, failed-dependency scheduling behavior, and reviewer minor-finding approval behavior.

## v2.1.1

- **Website metadata:** replaced hardcoded site version text with live GitHub/PyPI badges and direct latest-release links.
- **Publish workflow reliability:** release-triggered PyPI automation now avoids duplicate tag/release uploads while keeping manual re-run support.

## v2.1.0

- **Orchestration quality:** planner chunk-resume preflight, dependency-aware scheduling, role-batched coder/reviewer waves, and structured findings storage.
- **Production gates:** validator framework for placeholder/schema/project-integrity checks, configurable quality gates, and per-plan `quality_report.json` traceability.
- **Release automation:** GitHub Actions workflow added for automated PyPI publish on release/tag with Trusted Publishing support.

## v2.0.0

- **Unified UX:** `lao`, `lao init`, and `lao configure-models` now match the polished `lao run` visual language with guided, step-based TTY flows.
- **Operator continuity:** post-setup/post-config next actions (`health`/`run`) are integrated, reducing dead ends between commands.
- **Recovery clarity:** startup checks and model-remap guidance are surfaced as first-class interactive flows for smoother long-running operation.

## v1.3.0

- **Git:** Optional per-plan repo under **`./<plan-stem>/`**: snapshot **`LAO_PLAN.md`**, **`LAO_TASKS.json`** after architect, commits after coder/reviewer with **`lao(…):`** messages; **`LAO_REVIEW.log`** for review outcomes. Config **`git:`** in `factory.yaml`; CLI **`--no-git`**.
- **Site:** Redesigned GitHub Pages landing (hero, features, install).

## v1.2.0

- **Layout:** Per-plan code lives at **`./<plan-stem>/`** next to `plans/` (not under `.lao/workspaces/`). Fallback workspace **`.lao/_misc/`**.
- **CLI:** Rich **full-screen dashboard** on TTY (`--plain` for classic log). **`lao init`** adds workspace **`README.md`** when missing (`--skip-readme` to skip).
- **Fix:** SQLite no longer opens a bogus **`None`** database file when using the default `TaskQueue()` constructor.
- **Branding:** LAO palette on CLI, site, and docs.

## v1.1.1

- **Layout (superseded in v1.2.0):** `lao init` created **`.lao/workspaces/`** + **`plans/`**; per-plan workspace was **`.lao/workspaces/<plan-stem>/`** (from `plans/Foo.md` → `Foo`).
- **Plans:** Ignore **`plans/README.md`** when scanning for plans.
- **Defaults:** Reviewer model default **deepseek-r1-distill-qwen-32b** (adjust `key` to match `lms ls`).
- **Docs:** [docs/PYPI_PUBLISH.md](docs/PYPI_PUBLISH.md); local token notes template **`PYPI_PUBLISH.local.md`** (gitignored).

## v1.1.0

- **Reviewer:** Strip chain-of-thought (*think* tags) and detect `APPROVED` / `REJECTED` on any line — fixes false rejections from DeepSeek-R1–style reasoning before the verdict.
- Docs and landing page updated for this release.

## v1.0.0

- Initial stable release: `lao` CLI, planner / coder / reviewer pipeline, SQLite state, memory gate, GitHub Pages docs.
