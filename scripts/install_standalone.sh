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

# Source canonical serving defaults so LM_STUDIO_VERSION etc. are available.
# shellcheck source=/dev/null
[ -f "$SCRIPT_DIR/configs/lmstudio/defaults.sh" ] && . "$SCRIPT_DIR/configs/lmstudio/defaults.sh"

# Skipped root-only steps, reported at the end.
declare -a LMLA_SUDO_NOTES=()

# msg - print a highlighted progress line. Args: $* message text.
msg() { printf '\n==> %s\n' "$*"; }

# log - leveled diagnostic to stderr. Args: $1 level, $2 message.
log() { printf '[%s] %s\n' "$1" "$2" >&2; }

# need_cmd - return success when a command is available.
need_cmd() { command -v "$1" >/dev/null 2>&1; }

# note_sudo_required - record a root-only step that was skipped.
note_sudo_required() { LMLA_SUDO_NOTES+=("$1"); msg "SKIP (requires sudo): $1"; }

# report_sudo_required - print the collected skipped root steps (POSTFLIGHT).
report_sudo_required() {
  [ "${#LMLA_SUDO_NOTES[@]}" -gt 0 ] || return 0
  msg "The following steps were skipped because they need root. Re-run with: sudo bash install.sh"
  local note
  for note in "${LMLA_SUDO_NOTES[@]}"; do printf '    - %s\n' "$note"; done
}

# run_as_target - run "$@" as the target user with a user session environment.
run_as_target() {
  if [ "$(id -un)" = "$TARGET_USER" ]; then
    env HOME="$TARGET_HOME" USER="$TARGET_USER" LOGNAME="$TARGET_USER" \
      XDG_RUNTIME_DIR="$RUNTIME_DIR" DBUS_SESSION_BUS_ADDRESS="$USER_BUS" "$@"
  else
    sudo -H -u "$TARGET_USER" env HOME="$TARGET_HOME" USER="$TARGET_USER" LOGNAME="$TARGET_USER" \
      XDG_RUNTIME_DIR="$RUNTIME_DIR" DBUS_SESSION_BUS_ADDRESS="$USER_BUS" "$@"
  fi
}

# apt_install - install apt packages when root; otherwise record a sudo note.
apt_install() {
  if [ "$(id -u)" -ne 0 ]; then
    note_sudo_required "apt-get install $*"
    return 0
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

# reload_systemd_daemon - reload the target user's systemd manager.
reload_systemd_daemon() { run_as_target systemctl --user daemon-reload; }

# enable_systemd_service - enable a target user service. Args: $1 unit name.
enable_systemd_service() { run_as_target systemctl --user enable "$1"; }

# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/lm_studio.sh"

# Load the shared installer component framework (sibling checkout or download).
for __isc_d in "${ISC_FUNCTIONS_DIR:-}" \
               "$SCRIPT_DIR/../linux_installation_scripts_functions" \
               "$HOME/projects/linux_installation_scripts_functions"; do
  [ -n "$__isc_d" ] && [ -f "$__isc_d/component_loader.sh" ] && { source "$__isc_d/component_loader.sh"; break; }
done
declare -F isc_activate_components >/dev/null 2>&1 || \
  source <(curl -fsSL "https://raw.githubusercontent.com/mikaeltorni/linux_installation_scripts_functions/${ISC_FUNCTIONS_REF:-master}/component_loader.sh")
isc_activate_components
source "$SCRIPT_DIR/installer/components.sh"

# main - run the full install via the shared component runtime.
main() {
  msg "Installing LM Studio local agents for $TARGET_USER"
  component_main "$@"
  msg "LM Studio local agents installed. Ensure ~/.local/bin is on PATH."
}

# Listing/help must print only their own output (the master parses it).
case "${1:-}" in
  --list-components|--help|-h) component_main "$@"; exit $? ;;
esac

main "$@"
