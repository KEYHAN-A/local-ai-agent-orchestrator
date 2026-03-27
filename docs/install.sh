#!/usr/bin/env bash
# Thin bootstrap for https://lao.keyhan.info/install.sh (GitHub Pages).
# The real installer lives at scripts/install.sh so you can diff it against git.
set -euo pipefail
: "${LAO_INSTALL_SCRIPT_URL:=https://raw.githubusercontent.com/KEYHAN-A/local-ai-agent-orchestrator/main/scripts/install.sh}"
curl -fsSL "$LAO_INSTALL_SCRIPT_URL" | bash
