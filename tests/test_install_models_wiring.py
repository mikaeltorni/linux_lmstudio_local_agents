#!/usr/bin/env python3
"""Integration test for the models component wiring in lib/lm_studio.sh.

Sources the library with stubbed installer helpers and a throwaway TARGET_HOME,
then drives install_models down the non-interactive consumer path
(LMSTUDIO_INSTALL_MODELS) to prove the end-to-end effect: a customized catalog
is written into the user's config encoding exactly the selected models, switch
commands are generated only for installed switchable models, and the serve
component preserves that customized catalog instead of clobbering it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _run(home: Path, install_models_env: str, *, call: str) -> subprocess.CompletedProcess:
    """Source lib/lm_studio.sh with stubs and run the requested function(s)."""
    script = f"""
set -euo pipefail
TARGET_USER="$USER"
TARGET_HOME="{home}"
LMLA_DIR_OVERRIDE="{ROOT}"
# Stub the shared installer helpers the library expects to be defined.
run_as_target() {{ "$@"; }}
msg() {{ :; }}
log() {{ :; }}
note_sudo_required() {{ :; }}
apt_install() {{ :; }}
need_cmd() {{ command -v "$1" >/dev/null 2>&1; }}
reload_systemd_daemon() {{ :; }}
enable_systemd_service() {{ :; }}
systemctl() {{ :; }}
export LMSTUDIO_DOWNLOAD_MODELS=0
{install_models_env}
source "{ROOT}/lib/lm_studio.sh"
{call}
"""
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )


def _catalog(home: Path) -> dict:
    path = home / ".config/lmstudio-local-agents/configs/lmstudio/model-profiles.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_consumer_selection_writes_customized_catalog(tmp_path):
    """LMSTUDIO_INSTALL_MODELS installs only the named models in the catalog."""
    proc = _run(
        tmp_path,
        'export LMSTUDIO_INSTALL_MODELS="nex-n2-mini ornstein"',
        call="install_models",
    )
    assert proc.returncode == 0, proc.stderr
    catalog = _catalog(tmp_path)
    installed = [p["id"] for p in catalog["profiles"] if p["default_install"]]
    assert set(installed) == {"nex-n2-mini", "ornstein"}
    main = [p["id"] for p in catalog["profiles"] if p["default_main"]]
    assert main == ["nex-n2-mini"]  # nex carries default_main among the subset
    # Switch command exists for the switchable ornstein, not for the dropped qwen.
    bin_dir = tmp_path / ".local/bin"
    assert (bin_dir / "ornstein").exists()
    assert not (bin_dir / "qwencoder").exists()
    # Core tools are installed regardless.
    assert (bin_dir / "lmstudio-models").exists()
    assert (bin_dir / "lmstudio-model").exists()


def test_serve_preserves_customized_catalog(tmp_path):
    """Running serve after a customized models install must not reset the catalog."""
    first = _run(
        tmp_path,
        'export LMSTUDIO_INSTALL_MODELS="qwen-coder"',
        call="install_models",
    )
    assert first.returncode == 0, first.stderr
    before = _catalog(tmp_path)
    assert {p["id"] for p in before["profiles"] if p["default_install"]} == {"qwen-coder"}

    second = _run(tmp_path, "", call="configure_serving")
    assert second.returncode == 0, second.stderr
    after = _catalog(tmp_path)
    assert {p["id"] for p in after["profiles"] if p["default_install"]} == {"qwen-coder"}


def test_empty_selection_writes_core_only_catalog(tmp_path):
    """An empty selection (core-only) installs no models and no switch commands."""
    proc = _run(
        tmp_path,
        "",
        call=(
            "lmla_install_assets\n"
            "lmla_write_selected_catalog '{\"selected_ids\":[],\"autostart_ids\":[]}'\n"
            "lmla_download_models\n"
            "lmla_install_switch_commands"
        ),
    )
    assert proc.returncode == 0, proc.stderr
    catalog = _catalog(tmp_path)
    assert not any(p["default_install"] for p in catalog["profiles"])
    bin_dir = tmp_path / ".local/bin"
    for cmd in ("nex", "ornstein", "qwencoder"):
        assert not (bin_dir / cmd).exists()
    # The serving tools are still installed — the core is intact.
    assert (bin_dir / "lmstudio-models").exists()


def test_default_path_installs_full_catalog(tmp_path):
    """With no selection signal, the shipped default catalog (all models) is used."""
    proc = _run(tmp_path, "", call="install_models")
    assert proc.returncode == 0, proc.stderr
    catalog = _catalog(tmp_path)
    installed = {p["id"] for p in catalog["profiles"] if p["default_install"]}
    assert installed == {"qwen-coder", "nex-n2-mini", "ornstein"}
