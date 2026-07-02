#!/usr/bin/env bash
# install_standalone.sh — Install LM Studio local-agent serving.
#
# Defines the shared installer helpers (run_as_target, apt_install, systemd
# helpers, sudo reporting), sources lib/lm_studio.sh and the component manifest,
# then delegates component selection/execution to the shared component_main
# runtime. Root-optional: root-only steps (the LM Studio AppImage install) are
# skipped and reported when run without sudo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
RUNTIME_DIR="/run/user/${TARGET_UID}"
USER_BUS="unix:path=${RUNTIME_DIR}/bus"
DISPLAY_VAL="${DISPLAY:-:0}"

# Source canonical serving defaults so LM_STUDIO_VERSION etc. are available.
# shellcheck source=/dev/null
[ -f "$SCRIPT_DIR/configs/lmstudio/defaults.sh" ] && . "$SCRIPT_DIR/configs/lmstudio/defaults.sh"

# log - leveled diagnostic to stderr. Args: $1 level, $2 message. Defined before
# the shared helpers load so they emit through this repo's format.
log() { printf '[%s] %s\n' "$1" "$2" >&2; }

# Load the shared installer framework + shared install-time helpers (msg,
# need_cmd, run_as_target, apt_install, and the sudo-skip reporting used by the
# report_sudo_required POSTFLIGHT) before sourcing lib/lm_studio.sh or running any
# component. No vendored copy: resolve a sibling checkout or download on demand.
for __isc_d in "${ISC_FUNCTIONS_DIR:-}" \
               "$SCRIPT_DIR/../linux_installation_scripts_functions" \
               "$HOME/projects/linux_installation_scripts_functions"; do
  [ -n "$__isc_d" ] && [ -f "$__isc_d/component_loader.sh" ] && { source "$__isc_d/component_loader.sh"; break; }
done
declare -F isc_load_helpers >/dev/null 2>&1 || \
  source <(curl -fsSL "https://raw.githubusercontent.com/mikaeltorni/linux_installation_scripts_functions/${ISC_FUNCTIONS_REF:-master}/component_loader.sh")
isc_load_helpers

# reload_systemd_daemon - reload the target user's systemd manager.
reload_systemd_daemon() { run_as_target systemctl --user daemon-reload; }

# enable_systemd_service - enable a target user service. Args: $1 unit name.
enable_systemd_service() { run_as_target systemctl --user enable "$1"; }

# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/lm_studio.sh"

# The framework was already resolved above (for isc_load_helpers); activate the
# component runtime from the same checkout.
isc_activate_components
source "$SCRIPT_DIR/installer/components.sh"

# Signal whether this run may prompt interactively for per-model selection. The
# shared runtime only shows its component menu when no mode flag is given on a
# TTY; mirror that here so install_models launches the model-selection menu in
# the same case and stays silent for --default/--all/--select/automated runs.
LMLA_INTERACTIVE=1
for __lmla_arg in "$@"; do
  case "$__lmla_arg" in
    --default|--all|--select|--select=*|--list-components|--list-startup-components|--help|-h) LMLA_INTERACTIVE=0 ;;
  esac
done
export LMLA_INTERACTIVE

# main - run the full install via the shared component runtime.
main() {
  msg "Installing LM Studio local agents for $TARGET_USER"
  component_main "$@"
  msg "LM Studio local agents installed. Ensure ~/.local/bin is on PATH."
}

# Listing/help must print only their own output (the master parses it).
case "${1:-}" in
  --list-startup-components) lmla_list_startup_components; exit $? ;;
  --list-components|--list-configurable-components|--configure-component|--configure-component=*|--export-selection|--detect|--help|-h|--uninstall|--uninstall=*) component_main "$@"; exit $? ;;
esac

main "$@"
