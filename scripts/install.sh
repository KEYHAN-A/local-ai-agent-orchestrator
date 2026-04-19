#!/usr/bin/env bash
# Install LAO (local-ai-agent-orchestrator) from PyPI.
# Canonical copy: scripts/install.sh in the repo.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/KEYHAN-A/local-ai-agent-orchestrator/main/scripts/install.sh | bash
# Optional:
#   LAO_VERSION=3.1.0  pin a release (passed to pip/pipx as ==version)
#   LAO_PACKAGE=name   override PyPI package name (default: local-ai-agent-orchestrator)

set -euo pipefail

LAO_PACKAGE="${LAO_PACKAGE:-local-ai-agent-orchestrator}"
if [[ -n "${LAO_VERSION:-}" ]]; then
  LAO_SPEC="${LAO_PACKAGE}==${LAO_VERSION}"
else
  LAO_SPEC="${LAO_PACKAGE}"
fi

die() {
  echo "install.sh: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing '$1' on PATH"
}

py_ok() {
  local v
  v="$("$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  [[ "$(printf '%s\n3.10\n' "$v" | sort -V | head -n1)" == "3.10" ]]
}

pick_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || die "need Python 3.10+ (python3) on PATH"

echo "Using $($PY --version)"

user_base="$("$PY" -m site --user-base 2>/dev/null || true)"
if [[ -z "$user_base" ]]; then
  user_base="${HOME}/.local"
fi
user_bin="${user_base}/bin"

warn_path() {
  case ":${PATH}:" in
  *":${user_bin}:"*) ;;
  *)
    echo "" >&2
    echo "Note: ${user_bin} may not be on your PATH. Add it to your shell profile, e.g.:" >&2
    echo "  export PATH=\"${user_bin}:\$PATH\"" >&2
    ;;
  esac
}

if command -v pipx >/dev/null 2>&1; then
  echo "Installing with pipx (isolated app environment)…"
  if [[ -n "${LAO_VERSION:-}" ]]; then
    pipx install --force "${LAO_SPEC}"
  elif pipx list --short 2>/dev/null | grep -qx "${LAO_PACKAGE}"; then
    pipx upgrade "${LAO_PACKAGE}"
  else
    pipx install "${LAO_SPEC}"
  fi
else
  echo "pipx not found; installing with pip --user (consider: brew install pipx)…"
  need_cmd "$PY"
  "$PY" -m pip install --user --upgrade "${LAO_SPEC}"
  warn_path
fi

if command -v lao >/dev/null 2>&1; then
  lao --version
  echo ""
  echo "Done. Try: lao"
else
  warn_path
  if [[ -x "${user_bin}/lao" ]]; then
    echo "lao is installed at ${user_bin}/lao — run that or fix PATH as above."
  else
    die "install finished but 'lao' was not found on PATH"
  fi
fi
