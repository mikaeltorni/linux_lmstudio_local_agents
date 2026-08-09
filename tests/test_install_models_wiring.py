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


def test_models_component_is_default_off():
    """The menu default should not install or download LLM models unattended."""
    components = (ROOT / "installer" / "components.sh").read_text(encoding="utf-8")
    assert (
        "models|Choose models to install + autostart, or add your own"
        "|off|install_models|detect_models|uninstall_models"
    ) in components


def test_models_component_advertises_nested_configurator():
    """Both master and standalone menus must discover the model detail screen."""
    proc = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--list-configurable-components"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["models"]


def test_configure_component_writes_selection_without_downloading(tmp_path):
    """The nested screen contract should persist choices but perform no download."""
    selection = json.dumps({"selected_ids": ["ornstein"], "autostart_ids": ["ornstein"]})
    proc = _run(
        tmp_path,
        f"export LMLA_MODEL_SELECTION_JSON={selection!r}",
        call="configure_models",
    )
    assert proc.returncode == 0, proc.stderr
    catalog = _catalog(tmp_path)
    assert [p["id"] for p in catalog["profiles"] if p["default_install"]] == ["ornstein"]
    assert not (tmp_path / ".config/systemd/user/lmstudio-model-download.service").exists()
    before = json.dumps(catalog, sort_keys=True)
    repeated = _run(
        tmp_path,
        f"export LMLA_MODEL_SELECTION_JSON={selection!r}",
        call="configure_models",
    )
    assert repeated.returncode == 0, repeated.stderr
    assert json.dumps(_catalog(tmp_path), sort_keys=True) == before


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
        'export LMSTUDIO_INSTALL_MODELS="vibethinker ornstein"',
        call="install_models",
    )
    assert proc.returncode == 0, proc.stderr
    catalog = _catalog(tmp_path)
    installed = [p["id"] for p in catalog["profiles"] if p["default_install"]]
    assert set(installed) == {"vibethinker", "ornstein"}
    main = [p["id"] for p in catalog["profiles"] if p["default_main"]]
    assert main == ["ornstein"]
    # Switch command exists for Ornstein; keep-on vibethinker has no switch command.
    bin_dir = tmp_path / ".local/bin"
    assert (bin_dir / "ornstein").exists()
    assert not (bin_dir / "vibethinker").exists()
    # Core tools are installed regardless.
    assert (bin_dir / "lmstudio-models").exists()
    assert (bin_dir / "lmstudio-model").exists()


def test_fresh_additive_consumer_installs_only_requested_model(tmp_path):
    """An additive request on a clean home must not inherit every shipped default."""
    proc = _run(
        tmp_path,
        'export LMSTUDIO_ADD_MODELS="ornstein"\nexport LMSTUDIO_MAIN_MODEL="ornstein"',
        call="install_models",
    )
    assert proc.returncode == 0, proc.stderr
    installed = {
        profile["id"] for profile in _catalog(tmp_path)["profiles"]
        if profile["default_install"]
    }
    assert installed == {"ornstein"}


def test_serve_preserves_customized_catalog(tmp_path):
    """Running serve after a customized models install must not reset the catalog."""
    first = _run(
        tmp_path,
        'export LMSTUDIO_INSTALL_MODELS="vibethinker"',
        call="install_models",
    )
    assert first.returncode == 0, first.stderr
    before = _catalog(tmp_path)
    assert {p["id"] for p in before["profiles"] if p["default_install"]} == {"vibethinker"}

    second = _run(tmp_path, "", call="configure_serving")
    assert second.returncode == 0, second.stderr
    after = _catalog(tmp_path)
    assert {p["id"] for p in after["profiles"] if p["default_install"]} == {"vibethinker"}


def test_fresh_core_service_catalog_selects_no_models(tmp_path):
    """Core app/service setup must not make every shipped model look selected."""
    first = _run(tmp_path, "", call="configure_serving")
    assert first.returncode == 0, first.stderr
    catalog = _catalog(tmp_path)
    assert not any(profile["default_install"] for profile in catalog["profiles"])

    second = _run(
        tmp_path,
        'export LMSTUDIO_ADD_MODELS="vibethinker"',
        call="install_models",
    )
    assert second.returncode == 0, second.stderr
    selected = {
        profile["id"] for profile in _catalog(tmp_path)["profiles"]
        if profile["default_install"]
    }
    assert selected == {"vibethinker"}


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
    for cmd in ("ornstein", "vibethinker"):
        assert not (bin_dir / cmd).exists()
    # The serving tools are still installed — the core is intact.
    assert (bin_dir / "lmstudio-models").exists()


def test_additive_selection_preserves_existing_custom_models(tmp_path):
    """LMSTUDIO_ADD_MODELS adds requested ids without dropping custom selections."""
    custom_selection = (
        "lmla_install_assets\n"
        "lmla_write_selected_catalog '{\"selected_ids\":[\"my-model\"],"
        "\"autostart_ids\":[\"my-model\"],"
        "\"custom_profiles\":[{\"name\":\"My Model\",\"model_key\":\"my-model\","
        "\"download_url\":\"https://example.com/my-model.gguf\"}]}'"
    )
    first = _run(tmp_path, "", call=custom_selection)
    assert first.returncode == 0, first.stderr

    second = _run(
        tmp_path,
        'export LMSTUDIO_ADD_MODELS="ornstein"\nexport LMSTUDIO_MAIN_MODEL="ornstein"',
        call="install_models",
    )
    assert second.returncode == 0, second.stderr
    catalog = _catalog(tmp_path)
    installed = {p["id"] for p in catalog["profiles"] if p["default_install"]}
    main = [p["id"] for p in catalog["profiles"] if p["default_main"]]
    assert {"my-model", "ornstein"} <= installed
    assert main == ["ornstein"]


def test_custom_model_environment_adds_main_profile(tmp_path):
    """Consumer variables should add a downloadable custom main model."""
    proc = _run(
        tmp_path,
        "\n".join(
            [
                'export LMSTUDIO_ADD_CUSTOM_MODEL=1',
                'export LMSTUDIO_CUSTOM_MODEL_NAME="My Agent"',
                'export LMSTUDIO_CUSTOM_MODEL_KEY="my-agent"',
                'export LMSTUDIO_CUSTOM_MODEL_URL="https://example.com/my-agent.gguf"',
                'export LMSTUDIO_CUSTOM_MODEL_SWITCH_ENV_VAR="USE_MY_AGENT"',
                'export LMSTUDIO_CUSTOM_MODEL_ROLE="main"',
            ]
        ),
        call="install_models",
    )
    assert proc.returncode == 0, proc.stderr
    catalog = _catalog(tmp_path)
    custom = next(profile for profile in catalog["profiles"] if profile["id"] == "my-agent")
    assert custom["download_url"] == "https://example.com/my-agent.gguf"
    assert custom["switch_env_var"] == "USE_MY_AGENT"
    assert custom["default_main"] is True


def test_hugging_face_source_environment_uses_lm_studio_download_metadata(tmp_path):
    """One source variable must persist an lms-get spec, not a fake GGUF path."""
    proc = _run(
        tmp_path,
        "\n".join([
            "export LMSTUDIO_ADD_CUSTOM_MODEL=1",
            'export LMSTUDIO_CUSTOM_MODEL_SOURCE="owner/model-GGUF@Q4_K_M"',
            'export LMSTUDIO_CUSTOM_MODEL_ROLE="main"',
        ]),
        call="install_models",
    )
    assert proc.returncode == 0, proc.stderr
    custom = next(p for p in _catalog(tmp_path)["profiles"] if p["id"] == "model-gguf")
    assert custom["download_source"] == "owner/model-GGUF@Q4_K_M"
    assert custom["download_url"] == ""


def test_download_scripts_route_catalog_sources_through_lms_get():
    library = (ROOT / "lib" / "lm_studio.sh").read_text(encoding="utf-8")
    background = (ROOT / "scripts" / "lmstudio_download_models.sh").read_text(encoding="utf-8")
    assert 'lms get --gguf --yes "$download_source"' in library
    assert 'lms get --gguf --yes "$download_source"' in background


def test_default_path_installs_full_catalog(tmp_path):
    """With no selection signal, the shipped default catalog (all models) is used."""
    proc = _run(tmp_path, "", call="install_models")
    assert proc.returncode == 0, proc.stderr
    catalog = _catalog(tmp_path)
    installed = {p["id"] for p in catalog["profiles"] if p["default_install"]}
    assert installed == {"vibethinker", "ornstein"}


def test_downloads_run_in_background_not_inline(tmp_path):
    """A real install must not block on the multi-GB download.

    With downloads enabled, install_models installs the resumable download script
    and a systemd --user oneshot unit and starts it non-blocking, instead of
    fetching the weights inline (which would stall the master installer and every
    repository sequenced after it). No model file is fetched during install."""
    proc = _run(
        tmp_path,
        "\n".join(
            [
                'export LMSTUDIO_INSTALL_MODELS="vibethinker"',
                # Re-enable downloads (the harness defaults them off) to exercise
                # the real, non-test-skipped path.
                "export LMSTUDIO_DOWNLOAD_MODELS=1",
            ]
        ),
        call="install_models",
    )
    assert proc.returncode == 0, proc.stderr
    # The background download service and its script are installed...
    unit = tmp_path / ".config/systemd/user/lmstudio-model-download.service"
    script = tmp_path / ".local/bin/lmstudio-download-models"
    assert unit.exists(), "background download systemd unit was not installed"
    assert script.exists(), "background download script was not installed"
    assert "WantedBy=default.target" in unit.read_text(encoding="utf-8")
    # ...and nothing was downloaded inline during the install.
    models_dir = tmp_path / ".lmstudio/models"
    fetched = list(models_dir.rglob("*")) if models_dir.exists() else []
    assert not [p for p in fetched if p.is_file()], f"unexpected inline download: {fetched}"


def test_sync_override_downloads_inline(tmp_path):
    """LMSTUDIO_DOWNLOAD_MODELS_SYNC=1 forces the blocking inline path (no service)."""
    proc = _run(
        tmp_path,
        "\n".join(
            [
                'export LMSTUDIO_INSTALL_MODELS="vibethinker"',
                "export LMSTUDIO_DOWNLOAD_MODELS=0",  # skip the actual curl
                "export LMSTUDIO_DOWNLOAD_MODELS_SYNC=1",
            ]
        ),
        call="install_models",
    )
    assert proc.returncode == 0, proc.stderr
    unit = tmp_path / ".config/systemd/user/lmstudio-model-download.service"
    assert not unit.exists(), "sync path must not install the background service"


def test_uninstall_models_keeps_assets_serve_still_needs(tmp_path):
    """Deselecting the optional models feature must not disable the serve unit.

    serve's ExecStart is ~/.local/bin/lmstudio-boot.sh, which invokes
    lmstudio-models; both are shared assets, so uninstall_models has to leave
    them alone while the serve component is still installed.
    """
    installed = _run(tmp_path, "", call="configure_serving\nuninstall_models")
    assert installed.returncode == 0, installed.stderr
    bin_dir = tmp_path / ".local/bin"
    assert (bin_dir / "lmstudio-boot.sh").exists()
    assert (bin_dir / "lmstudio-models").exists()
    unit = tmp_path / ".config/systemd/user/lmstudio-serve.service"
    exec_start = [
        line for line in unit.read_text(encoding="utf-8").splitlines()
        if line.startswith("ExecStart=")
    ][0]
    assert Path(exec_start.split("=", 1)[1]).exists()


def test_uninstall_models_removes_assets_when_nothing_else_needs_them(tmp_path):
    """With no serve unit present the helpers are still fully cleaned up."""
    proc = _run(
        tmp_path,
        'export LMSTUDIO_INSTALL_MODELS="ornstein"',
        call="install_models\nuninstall_models",
    )
    assert proc.returncode == 0, proc.stderr
    bin_dir = tmp_path / ".local/bin"
    assert not (bin_dir / "lmstudio-boot.sh").exists()
    assert not (bin_dir / "lmstudio-models").exists()
