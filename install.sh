#!/usr/bin/env bash
# install.sh — Linux LM Studio Local Agents installer entry point.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SCRIPT_DIR/scripts/install_standalone.sh" ]; then
  echo "Error: scripts/install_standalone.sh not found in linux_lmstudio_local_agents" >&2
  exit 1
fi

exec bash "$SCRIPT_DIR/scripts/install_standalone.sh" "$@"
