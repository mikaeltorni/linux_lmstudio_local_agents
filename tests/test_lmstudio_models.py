#!/usr/bin/env python3
"""Tests for the LM Studio model registry and resolution logic.

These exercise the pure planning/resolution helpers against the real registry in
configs/lmstudio/model-profiles.json, so the shipped default layout (qwen-coder
kept on, Nex main, Ornstein switchable) is locked in, plus the env-var switching
precedence and the validation rules.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "lmstudio_models.py"
CATALOG = ROOT / "configs" / "lmstudio" / "model-profiles.json"


def _load_module():
    """Load the lmstudio_models module under test.

    The module is registered in ``sys.modules`` before execution so that its
    ``@dataclass`` resolves annotations (it uses ``from __future__ import
    annotations``) without a missing-module error during dynamic import.
    """
    spec = importlib.util.spec_from_file_location("lmstudio_models", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lm = _load_module()


@pytest.fixture()
def registry():
    """Return the real shipped registry."""
    return lm.load_registry(CATALOG)


def test_registry_requires_profiles(tmp_path):
    """load_registry rejects a file without a profiles list."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")
    with pytest.raises(ValueError):
        lm.load_registry(bad)


def test_main_env_var_default_and_value(registry, tmp_path):
    """main_env_var reads the registry value, with a safe fallback."""
    assert lm.main_env_var(registry) == "LMSTUDIO_MAIN_MODEL"
    minimal = tmp_path / "m.json"
    minimal.write_text(json.dumps({"profiles": []}), encoding="utf-8")
    assert lm.main_env_var(lm.load_registry(minimal)) == lm.DEFAULT_MAIN_ENV_VAR


def test_find_profile_by_id_command_and_key(registry):
    """Profiles resolve by id, command alias, and model key."""
    assert lm.find_profile(registry, "nex-n2-mini")["id"] == "nex-n2-mini"
    assert lm.find_profile(registry, "nex")["id"] == "nex-n2-mini"
    assert lm.find_profile(registry, "ornstein3.6-35b-a3b")["id"] == "ornstein"
    assert lm.find_profile(registry, "nope") is None


def test_default_plan_matches_shipped_layout(registry):
    """The default plan encodes the user's required default model layout."""
    plan = lm.default_plan(registry)
    assert set(plan.install_ids) == {"qwen-coder", "nex-n2-mini", "ornstein"}
    assert plan.keep_on_ids == ("qwen-coder",)
    assert plan.main_id == "nex-n2-mini"
    assert plan.main_env_var == "LMSTUDIO_MAIN_MODEL"
    assert plan.switch_env_vars == {
        "qwen-coder": "LMSTUDIO_USE_QWEN_CODER",
        "nex-n2-mini": "LMSTUDIO_USE_NEX",
        "ornstein": "LMSTUDIO_USE_ORNSTEIN",
    }


def test_default_plan_keep_on_model_keys(registry):
    """Keep-on resolves to the qwen coder model key."""
    plan = lm.default_plan(registry)
    assert plan.keep_on_model_keys(registry) == ["qwen2.5-coder-1.5b-instruct"]


def test_resolve_active_main_defaults_to_nex(registry):
    """With no switch variables set, the active main model is Nex."""
    plan = lm.default_plan(registry)
    assert lm.resolve_active_main(registry, plan, env={})["model_key"] == "nex-n2-mini"


def test_resolve_active_main_switch_var_wins(registry):
    """A truthy per-model switch variable switches the active main model."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_ORNSTEIN": "1"})
    assert active["model_key"] == "ornstein3.6-35b-a3b"


def test_resolve_active_main_switch_var_precedence_is_registry_order(registry):
    """When multiple switch vars are set, the first in registry order wins."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(
        registry, plan, env={"LMSTUDIO_USE_NEX": "yes", "LMSTUDIO_USE_ORNSTEIN": "on"}
    )
    # nex appears before ornstein in the registry, so it wins.
    assert active["model_key"] == "nex-n2-mini"


def test_resolve_active_main_main_env_var_names_model(registry):
    """LMSTUDIO_MAIN_MODEL selects an installed model when no switch var is set."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_MAIN_MODEL": "ornstein"})
    assert active["model_key"] == "ornstein3.6-35b-a3b"


def test_resolve_active_main_switch_var_beats_main_env_var(registry):
    """A per-model switch var takes precedence over LMSTUDIO_MAIN_MODEL."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(
        registry, plan, env={"LMSTUDIO_MAIN_MODEL": "nex-n2-mini", "LMSTUDIO_USE_ORNSTEIN": "1"}
    )
    assert active["model_key"] == "ornstein3.6-35b-a3b"


def test_resolve_active_main_ignores_uninstalled_main_env_target(registry):
    """An LMSTUDIO_MAIN_MODEL that names an uninstalled model is ignored."""
    plan = lm.build_plan(
        registry, install_ids=["nex-n2-mini", "qwen-coder"], keep_on_ids=["qwen-coder"], main_id="nex-n2-mini"
    )
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_MAIN_MODEL": "ornstein"})
    assert active["model_key"] == "nex-n2-mini"


def test_falsey_switch_var_does_not_switch(registry):
    """A non-truthy switch value does not change the active model."""
    plan = lm.default_plan(registry)
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_ORNSTEIN": "0"})["model_key"] == "nex-n2-mini"
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_ORNSTEIN": ""})["model_key"] == "nex-n2-mini"


def test_build_plan_rejects_empty_install(registry):
    """An empty install set is rejected."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=[], keep_on_ids=[], main_id="nex-n2-mini")


def test_build_plan_rejects_unknown_id(registry):
    """Unknown model ids are rejected."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["mystery"], keep_on_ids=[], main_id="mystery")


def test_build_plan_rejects_keep_on_not_installed(registry):
    """Keep-on models must be part of the install set."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["nex-n2-mini"], keep_on_ids=["ornstein"], main_id="nex-n2-mini")


def test_build_plan_rejects_main_not_installed(registry):
    """The main model must be part of the install set."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["qwen-coder"], keep_on_ids=["qwen-coder"], main_id="nex-n2-mini")


def test_build_plan_switch_var_override(registry):
    """Explicit switch-var overrides win over the registry defaults."""
    plan = lm.build_plan(
        registry,
        install_ids=["nex-n2-mini", "ornstein"],
        keep_on_ids=[],
        main_id="nex-n2-mini",
        switch_env_vars={"ornstein": "LMSTUDIO_BACKUP"},
    )
    assert plan.switch_env_vars["ornstein"] == "LMSTUDIO_BACKUP"
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_BACKUP": "1"})["model_key"] == "ornstein3.6-35b-a3b"


def test_cli_active_main_and_keep_on(registry, capsys):
    """The CLI prints the default active main and keep-on model keys."""
    assert lm.main(["active-main", "--catalog", str(CATALOG)]) == 0
    assert capsys.readouterr().out.strip() == "nex-n2-mini"
    assert lm.main(["keep-on", "--catalog", str(CATALOG)]) == 0
    assert capsys.readouterr().out.strip() == "qwen2.5-coder-1.5b-instruct"


def test_cli_default_plan_json(capsys):
    """The default-plan subcommand emits valid JSON describing the plan."""
    assert lm.main(["default-plan", "--catalog", str(CATALOG)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["main_id"] == "nex-n2-mini"
    assert payload["keep_on_ids"] == ["qwen-coder"]


def test_boot_load_specs_default_order(registry):
    """Boot load order is keep-on models first, then the active main."""
    plan = lm.default_plan(registry)
    specs = lm.boot_load_specs(registry, plan, env={})
    keys = [spec["model_key"] for spec in specs]
    assert keys == ["qwen2.5-coder-1.5b-instruct", "nex-n2-mini"]
    # Each spec carries the lms load flags.
    assert set(lm.BOOT_LOAD_FIELDS).issubset(specs[0].keys())
    assert specs[0]["gpu"] == "off" and specs[1]["gpu"] == "max"


def test_boot_load_specs_follows_switch_var(registry):
    """A switch var changes the main model loaded after the keep-on models."""
    plan = lm.default_plan(registry)
    specs = lm.boot_load_specs(registry, plan, env={"LMSTUDIO_USE_ORNSTEIN": "1"})
    assert [spec["model_key"] for spec in specs] == ["qwen2.5-coder-1.5b-instruct", "ornstein3.6-35b-a3b"]


def test_boot_load_specs_no_duplicate_when_main_is_keep_on(registry):
    """If the active main is also a keep-on model it is not loaded twice."""
    plan = lm.build_plan(
        registry, install_ids=["qwen-coder"], keep_on_ids=["qwen-coder"], main_id="qwen-coder"
    )
    specs = lm.boot_load_specs(registry, plan, env={})
    assert [spec["model_key"] for spec in specs] == ["qwen2.5-coder-1.5b-instruct"]


def test_installable_ids_default_is_all_default_install(registry):
    """With no selection, every default_install model is installable."""
    assert set(lm.installable_ids(registry)) == {"qwen-coder", "nex-n2-mini", "ornstein"}


def test_installable_ids_honors_selection(registry):
    """A selection installs only the named models (programming-setup case)."""
    assert lm.installable_ids(registry, ["qwen-coder"]) == ["qwen-coder"]


def test_installable_ids_rejects_unknown(registry):
    """An unknown id in the selection raises."""
    with pytest.raises(ValueError):
        lm.installable_ids(registry, ["qwen-coder", "nope"])


def test_download_specs_only_qwen_coder(registry):
    """download_specs for the qwen-coder selection yields just that model."""
    specs = lm.download_specs(registry, ["qwen-coder"])
    assert [spec["id"] for spec in specs] == ["qwen-coder"]
    assert specs[0]["model_path"] == "unsloth/Qwen2.5-Coder-1.5B-Instruct-GGUF/Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf"
    assert set(lm.DOWNLOAD_FIELDS).issubset(specs[0].keys())


def test_cli_download_list_honors_env(registry, capsys, monkeypatch):
    """download-list restricts output to LMSTUDIO_INSTALL_MODELS when set."""
    monkeypatch.setenv("LMSTUDIO_INSTALL_MODELS", "qwen-coder")
    assert lm.main(["download-list", "--catalog", str(CATALOG)]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].split("\t")[0] == "qwen-coder"


def test_cli_download_list_default_lists_all(registry, capsys, monkeypatch):
    """Without the env var, download-list lists every default_install model."""
    monkeypatch.delenv("LMSTUDIO_INSTALL_MODELS", raising=False)
    assert lm.main(["download-list", "--catalog", str(CATALOG)]) == 0
    ids = {line.split("\t")[0] for line in capsys.readouterr().out.splitlines() if line.strip()}
    assert ids == {"qwen-coder", "nex-n2-mini", "ornstein"}
