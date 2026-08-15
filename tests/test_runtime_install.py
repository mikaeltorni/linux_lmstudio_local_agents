#!/usr/bin/env python3
"""Regression tests for installing the LM Studio app and headless daemon."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "lib" / "lm_studio.sh"


def run_bash(script: str, home: Path) -> subprocess.CompletedProcess[str]:
    """Run an isolated Bash installer probe."""
    return subprocess.run(
        ["bash", "-c", script],
        cwd=home,
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        text=True,
        capture_output=True,
        check=False,
    )


def test_core_repairs_headless_daemon_before_configuring_serving(tmp_path):
    """The unconditional core must install a working lms before its service."""
    marker = tmp_path / "calls"
    result = run_bash(
        f"""
set -euo pipefail
source {str(LIBRARY)!r}
install_lm_studio_app() {{ echo app >> {str(marker)!r}; }}
install_lmstudio_daemon() {{ echo daemon >> {str(marker)!r}; }}
configure_serving() {{ echo serve >> {str(marker)!r}; }}
msg() {{ :; }}
install_lmstudio_core
""",
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == [
        "app",
        "daemon",
        "serve",
    ]


def test_broken_lms_is_reinstalled_with_official_daemon_installer(tmp_path):
    """An executable that crashes must not make daemon installation look healthy."""
    lms = tmp_path / ".lmstudio" / "bin" / "lms"
    lms.parent.mkdir(parents=True)
    lms.write_text("#!/bin/sh\nexit 139\n", encoding="utf-8")
    lms.chmod(0o755)

    fake_installer = tmp_path / "official-install.sh"
    fake_installer.write_text(
        """#!/bin/sh
set -eu
mkdir -p "$HOME/.lmstudio/bin"
printf '#!/bin/sh\nexit 0\n' > "$HOME/.lmstudio/bin/lms"
chmod 0755 "$HOME/.lmstudio/bin/lms"
""",
        encoding="utf-8",
    )
    fake_installer.chmod(0o755)

    result = run_bash(
        f"""
set -euo pipefail
TARGET_HOME={str(tmp_path)!r}
TARGET_USER=$(id -un)
FAKE_INSTALLER={str(fake_installer)!r}
source {str(LIBRARY)!r}
msg() {{ :; }}
need_cmd() {{ command -v "$1" >/dev/null 2>&1; }}
run_as_target() {{ "$@"; }}
curl() {{
  local output=""
  while (( $# )); do
    if [[ "$1" == "-o" ]]; then output="$2"; shift 2; else shift; fi
  done
  cp "$FAKE_INSTALLER" "$output"
}}
install_lmstudio_daemon
""",
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert subprocess.run([str(lms), "--help"], check=False).returncode == 0


def test_app_install_is_isolatable_and_validates_extraction_before_success():
    """App extraction must happen in its temp dir and publish only valid files."""
    source = LIBRARY.read_text(encoding="utf-8")

    assert 'LMSTUDIO_APP_INSTALL_DIR:-/opt/lm-studio' in source
    assert 'LMSTUDIO_APP_WRAPPER_PATH:-/usr/local/bin/lm-studio' in source
    assert '(cd "$tmpdir"' in source
    assert 'test -x "$staging/lm-studio"' in source
    assert 'test -x "$staging/AppRun"' in source


def test_app_install_extracts_in_tempdir_and_publishes_valid_tree(tmp_path):
    """A successful fake AppImage replaces the app only after validation."""
    fake_appimage = tmp_path / "fake.AppImage"
    fake_appimage.write_text(
        """#!/bin/sh
set -eu
test "$1" = --appimage-extract
mkdir -p squashfs-root
printf '#!/bin/sh\nexit 0\n' > squashfs-root/lm-studio
chmod 0755 squashfs-root/lm-studio
""",
        encoding="utf-8",
    )
    fake_appimage.chmod(0o755)
    install_dir = tmp_path / "opt" / "lm-studio"
    install_dir.mkdir(parents=True)
    (install_dir / "stale").write_text("old", encoding="utf-8")
    wrapper = tmp_path / "bin" / "lm-studio"

    result = run_bash(
        f"""
set -euo pipefail
TARGET_HOME={str(tmp_path)!r}
TARGET_USER=$(id -un)
FAKE_APPIMAGE={str(fake_appimage)!r}
LMSTUDIO_APP_INSTALL_DIR={str(install_dir)!r}
LMSTUDIO_APP_WRAPPER_PATH={str(wrapper)!r}
source {str(LIBRARY)!r}
msg() {{ :; }}
apt_install() {{ :; }}
note_sudo_required() {{ return 1; }}
id() {{ if [[ "${{1:-}}" == -u ]]; then echo 0; else command id "$@"; fi; }}
dpkg() {{ echo amd64; }}
curl() {{
  local output=""
  while (( $# )); do
    if [[ "$1" == "-o" ]]; then output="$2"; shift 2; else shift; fi
  done
  cp "$FAKE_APPIMAGE" "$output"
}}
install_lm_studio_app
""",
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert (install_dir / "lm-studio").is_file()
    assert not (install_dir / "stale").exists()
    assert (install_dir / ".lmstudio-local-agents-version").read_text().strip() == (
        "0.4.21-2"
    )
    assert wrapper.is_file()
    assert not (tmp_path / "squashfs-root").exists()


def test_default_app_version_tracks_current_stable_linux_release():
    """The clean installer should use the current stable Linux AppImage."""
    source = LIBRARY.read_text(encoding="utf-8")

    assert 'LM_STUDIO_VERSION:-0.4.21-2' in source
