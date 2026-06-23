#!/usr/bin/env python3
"""Structural tests for the LM Studio CLI filesystem layout.

LMSTUDIO_BIN_DIR is the single source of truth for where the `lms` CLI lives.
The boot script and external consumers (e.g. agent_command_center's
lmstudio-log-runner) must resolve it from defaults.sh instead of hardcoding
"~/.lmstudio/bin", so a non-default install location only has to change here.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "configs" / "lmstudio" / "defaults.sh"
BOOT = ROOT / "configs" / "lmstudio" / "lmstudio-boot.sh"
INSTALLER = ROOT / "scripts" / "install_standalone.sh"


def test_defaults_define_overridable_bin_dir():
    """defaults.sh exports an overridable LMSTUDIO_BIN_DIR with the conventional default."""
    source = DEFAULTS.read_text(encoding="utf-8")
    assert 'LMSTUDIO_BIN_DIR="${LMSTUDIO_BIN_DIR:-$HOME/.lmstudio/bin}"' in source


def test_boot_script_uses_bin_dir_variable():
    """The boot script resolves the lms CLI via LMSTUDIO_BIN_DIR, not a literal path."""
    source = BOOT.read_text(encoding="utf-8")
    assert 'LMS_CLI="${LMSTUDIO_BIN_DIR}/lms"' in source
    assert 'export PATH="${LMSTUDIO_BIN_DIR}:' in source
    # The old hardcoded CLI path must be gone.
    assert 'LMS_CLI="${HOME}/.lmstudio/bin/lms"' not in source


def test_boot_script_restores_persisted_selection():
    """Boot honors the last lmstudio-model selection when no env switch is set."""
    source = BOOT.read_text(encoding="utf-8")
    assert 'SELECTED_MODEL_FILE="${HOME}/.config/lm-studio/selected-model"' in source
    assert 'LMSTUDIO_MAIN_MODEL="$(tr -d \'[:space:]\' < "$SELECTED_MODEL_FILE")"' in source
    assert "env | grep -Eq '^LMSTUDIO_USE_[A-Z0-9_]+=.+$'" in source


def test_boot_script_recovers_unauthable_daemon():
    """Cold boot can leave a daemon whose passkey the CLI rejects; recover by
    re-killing and bringing it back, not by polling a stuck daemon once."""
    source = BOOT.read_text(encoding="utf-8")
    # Configurable attempt budget with a sane default.
    assert 'LMSTUDIO_DAEMON_MAX_ATTEMPTS="${LMSTUDIO_DAEMON_MAX_ATTEMPTS:-12}"' in source
    # The recovery loops over attempts instead of hard-failing on the first miss.
    assert 'for attempt in $(seq 1 "${LMSTUDIO_DAEMON_MAX_ATTEMPTS}"); do' in source
    # Each attempt checks authentication first (leave a healthy daemon alone) and
    # tears the daemon down before bringing it back up when auth fails.
    assert 'if "$LMS_CLI" ps --json >/dev/null 2>&1; then' in source
    assert "pkill -u \"$(id -un)\" -f '(^|/)llmster( |$)'" in source
    assert '"$LMS_CLI" daemon up' in source
    # The passkey files must never be deleted — the CLI reads them and the daemon
    # regenerates them; removing them yields ENOENT instead of a fresh key.
    import re
    assert not re.search(r"\brm\b[^\n]*lms-key", source)


def test_defaults_define_daemon_max_attempts():
    """defaults.sh and the generated serve.env carry the daemon retry budget."""
    assert 'LMSTUDIO_DAEMON_MAX_ATTEMPTS="12"' in DEFAULTS.read_text(encoding="utf-8")
    lib = (ROOT / "lib" / "lm_studio.sh").read_text(encoding="utf-8")
    assert 'LMSTUDIO_DAEMON_MAX_ATTEMPTS="${LMSTUDIO_DAEMON_MAX_ATTEMPTS:-12}"' in lib


def test_python_logging_helper_is_installed_with_selector():
    """The installed selector has its centralized logging dependency beside it."""
    source = (ROOT / "lib" / "lm_studio.sh").read_text(encoding="utf-8")
    assert 'install -D -m 0644 "$LMLA_DIR/scripts/lmla_logging_utils.py" "$bin_dir/lmla_logging_utils.py"' in source
    assert 'install -D -m 0755 "$LMLA_DIR/scripts/lmstudio_model_selector.py" "$bin_dir/lmstudio-model"' in source


def test_standalone_installer_defines_shared_helper_session_env():
    """The shared run_as_target helper needs DISPLAY_VAL under set -u."""
    source = INSTALLER.read_text(encoding="utf-8")
    assert 'RUNTIME_DIR="/run/user/${TARGET_UID}"' in source
    assert 'USER_BUS="unix:path=${RUNTIME_DIR}/bus"' in source
    assert 'DISPLAY_VAL="${DISPLAY:-:0}"' in source
