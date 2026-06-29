#!/usr/bin/env python3
"""Tests for the LM Studio model registry and resolution logic.

These exercise the pure planning/resolution helpers against the real registry in
configs/lmstudio/model-profiles.json, so the shipped default layout (vibethinker
kept on and Ornstein main) is locked in, plus the env-var switching
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
    assert lm.find_profile(registry, "vibethinker")["id"] == "vibethinker"
    assert lm.find_profile(registry, "vibethinker")["id"] == "vibethinker"
    assert lm.find_profile(registry, "ornstein3.6-35b-a3b")["id"] == "ornstein"
    assert lm.find_profile(registry, "nope") is None


def test_default_plan_matches_shipped_layout(registry):
    """The default plan encodes the user's required default model layout."""
    plan = lm.default_plan(registry)
    assert set(plan.install_ids) == {"vibethinker", "ornstein"}
    assert plan.keep_on_ids == ("vibethinker",)
    assert plan.main_id == "ornstein"
    assert plan.main_env_var == "LMSTUDIO_MAIN_MODEL"
    assert plan.switch_env_vars == {
        "vibethinker": "LMSTUDIO_USE_VIBETHINKER",
        "ornstein": "LMSTUDIO_USE_ORNSTEIN",
    }


def test_default_plan_keep_on_model_keys(registry):
    """Keep-on resolves to the vibethinker model key."""
    plan = lm.default_plan(registry)
    assert plan.keep_on_model_keys(registry) == ["vibethinker-1.5b"]


def test_resolve_active_main_defaults_to_ornstein(registry):
    """With no switch variables set, the active main model is Ornstein."""
    plan = lm.default_plan(registry)
    assert lm.resolve_active_main(registry, plan, env={})["model_key"] == "ornstein3.6-35b-a3b"


def test_resolve_active_main_switch_var_wins(registry):
    """A truthy per-model switch variable switches the active main model."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_VIBETHINKER": "1"})
    assert active["model_key"] == "vibethinker-1.5b"


def test_resolve_active_main_switch_var_precedence_is_registry_order(registry):
    """When multiple switch vars are set, the first in registry order wins."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(
        registry, plan, env={"LMSTUDIO_USE_VIBETHINKER": "yes", "LMSTUDIO_USE_ORNSTEIN": "on"}
    )
    assert active["model_key"] == "vibethinker-1.5b"


def test_resolve_active_main_main_env_var_names_model(registry):
    """LMSTUDIO_MAIN_MODEL selects an installed model when no switch var is set."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_MAIN_MODEL": "vibethinker"})
    assert active["model_key"] == "vibethinker-1.5b"


def test_resolve_active_main_switch_var_beats_main_env_var(registry):
    """A per-model switch var takes precedence over LMSTUDIO_MAIN_MODEL."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(
        registry, plan, env={"LMSTUDIO_MAIN_MODEL": "vibethinker", "LMSTUDIO_USE_ORNSTEIN": "1"}
    )
    assert active["model_key"] == "ornstein3.6-35b-a3b"


def test_resolve_active_main_ignores_uninstalled_main_env_target(registry):
    """An LMSTUDIO_MAIN_MODEL that names an uninstalled model is ignored."""
    plan = lm.build_plan(
        registry, install_ids=["ornstein"], keep_on_ids=[], main_id="ornstein"
    )
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_MAIN_MODEL": "vibethinker"})
    assert active["model_key"] == "ornstein3.6-35b-a3b"


def test_falsey_switch_var_does_not_switch(registry):
    """A non-truthy switch value does not change the active model."""
    plan = lm.default_plan(registry)
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_VIBETHINKER": "0"})["model_key"] == "ornstein3.6-35b-a3b"
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_VIBETHINKER": ""})["model_key"] == "ornstein3.6-35b-a3b"


def test_build_plan_rejects_empty_install(registry):
    """An empty install set is rejected."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=[], keep_on_ids=[], main_id="ornstein")


def test_build_plan_rejects_unknown_id(registry):
    """Unknown model ids are rejected."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["mystery"], keep_on_ids=[], main_id="mystery")


def test_build_plan_rejects_keep_on_not_installed(registry):
    """Keep-on models must be part of the install set."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["ornstein"], keep_on_ids=["vibethinker"], main_id="ornstein")


def test_build_plan_rejects_main_not_installed(registry):
    """The main model must be part of the install set."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["vibethinker"], keep_on_ids=["vibethinker"], main_id="ornstein")


def test_build_plan_switch_var_override(registry):
    """Explicit switch-var overrides win over the registry defaults."""
    plan = lm.build_plan(
        registry,
        install_ids=["vibethinker", "ornstein"],
        keep_on_ids=[],
        main_id="ornstein",
        switch_env_vars={"vibethinker": "LMSTUDIO_BACKUP"},
    )
    assert plan.switch_env_vars["vibethinker"] == "LMSTUDIO_BACKUP"
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_BACKUP": "1"})["model_key"] == "vibethinker-1.5b"


def test_cli_active_main_and_keep_on(registry, capsys):
    """The CLI prints the default active main and keep-on model keys."""
    assert lm.main(["active-main", "--catalog", str(CATALOG)]) == 0
    assert capsys.readouterr().out.strip() == "ornstein3.6-35b-a3b"
    assert lm.main(["keep-on", "--catalog", str(CATALOG)]) == 0
    assert capsys.readouterr().out.strip() == "vibethinker-1.5b"


def test_cli_default_plan_json(capsys):
    """The default-plan subcommand emits valid JSON describing the plan."""
    assert lm.main(["default-plan", "--catalog", str(CATALOG)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["main_id"] == "ornstein"
    assert payload["keep_on_ids"] == ["vibethinker"]


def test_boot_load_specs_default_order(registry):
    """Boot load order is keep-on models first, then the active main."""
    plan = lm.default_plan(registry)
    specs = lm.boot_load_specs(registry, plan, env={})
    keys = [spec["model_key"] for spec in specs]
    assert keys == ["vibethinker-1.5b", "ornstein3.6-35b-a3b"]
    # Each spec carries the lms load flags.
    assert set(lm.BOOT_LOAD_FIELDS).issubset(specs[0].keys())
    assert specs[0]["gpu"] == "off" and specs[1]["gpu"] == "max"


def test_boot_load_specs_follows_switch_var(registry):
    """A switch var changes the main model loaded after the keep-on models."""
    plan = lm.default_plan(registry)
    specs = lm.boot_load_specs(registry, plan, env={"LMSTUDIO_USE_ORNSTEIN": "1"})
    assert [spec["model_key"] for spec in specs] == ["vibethinker-1.5b", "ornstein3.6-35b-a3b"]


def test_boot_load_specs_no_duplicate_when_main_is_keep_on(registry):
    """If the active main is also a keep-on model it is not loaded twice."""
    plan = lm.build_plan(
        registry, install_ids=["vibethinker"], keep_on_ids=["vibethinker"], main_id="vibethinker"
    )
    specs = lm.boot_load_specs(registry, plan, env={})
    assert [spec["model_key"] for spec in specs] == ["vibethinker-1.5b"]


def test_installable_ids_default_is_all_default_install(registry):
    """With no selection, every default_install model is installable."""
    assert set(lm.installable_ids(registry)) == {"vibethinker", "ornstein"}


def test_installable_ids_honors_selection(registry):
    """A selection installs only the named models (programming-setup case)."""
    assert lm.installable_ids(registry, ["vibethinker"]) == ["vibethinker"]


def test_installable_ids_rejects_unknown(registry):
    """An unknown id in the selection raises."""
    with pytest.raises(ValueError):
        lm.installable_ids(registry, ["vibethinker", "nope"])


def test_download_specs_only_vibethinker(registry):
    """download_specs for the vibethinker selection yields just that model."""
    specs = lm.download_specs(registry, ["vibethinker"])
    assert [spec["id"] for spec in specs] == ["vibethinker"]
    assert specs[0]["model_path"] == "mradermacher/VibeThinker-1.5B-GGUF/VibeThinker-1.5B.Q4_K_M.gguf"
    assert set(lm.DOWNLOAD_FIELDS).issubset(specs[0].keys())


def test_cli_download_list_honors_env(registry, capsys, monkeypatch):
    """download-list restricts output to LMSTUDIO_INSTALL_MODELS when set."""
    monkeypatch.setenv("LMSTUDIO_INSTALL_MODELS", "vibethinker")
    assert lm.main(["download-list", "--catalog", str(CATALOG)]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].split("\t")[0] == "vibethinker"


def test_cli_download_list_default_lists_all(registry, capsys, monkeypatch):
    """Without the env var, download-list lists every default_install model."""
    monkeypatch.delenv("LMSTUDIO_INSTALL_MODELS", raising=False)
    assert lm.main(["download-list", "--catalog", str(CATALOG)]) == 0
    ids = {line.split("\t")[0] for line in capsys.readouterr().out.splitlines() if line.strip()}
    assert ids == {"vibethinker", "ornstein"}


# --- Custom model normalization -------------------------------------------------


def test_slugify_and_switch_env_var():
    """Free-text names become safe slugs and switch variables."""
    assert lm.slugify_id("My Cool Model 7B!") == "my-cool-model-7b"
    assert lm.switch_env_var_for("my-cool-model-7b") == "LMSTUDIO_USE_MY_COOL_MODEL_7B"


def test_normalize_custom_profile_derives_fields():
    """A minimal custom spec fills in command, switch var, and download path."""
    profile = lm.normalize_custom_profile({
        "name": "Tiny Llama 1B",
        "model_key": "tinyllama-1b",
        "download_url": "https://example.com/repo/TinyLlama-1B.gguf?download=true",
    })
    assert profile["id"] == "tiny-llama-1b"
    assert profile["switch_env_var"] == "LMSTUDIO_USE_TINY_LLAMA_1B"
    assert profile["model_path"] == "tiny-llama-1b/TinyLlama-1B.gguf"
    assert profile["default_install"] is True
    assert profile["custom"] is True
    # Serving defaults are applied so the profile behaves like a shipped one.
    assert profile["gpu"] == "max" and profile["context_length"] == 131072
    assert profile["sha256"] == ""


def test_normalize_custom_profile_accepts_explicit_or_empty_switch_variable():
    """Coding models can name a switch variable; keep-on models can omit one."""
    explicit = lm.normalize_custom_profile({
        "name": "Agent Model",
        "model_key": "agent-model",
        "switch_env_var": "USE_AGENT_MODEL",
    })
    keep_on = lm.normalize_custom_profile({
        "name": "Autocomplete Model",
        "model_key": "autocomplete-model",
        "switch_env_var": "",
    })

    assert explicit["switch_env_var"] == "USE_AGENT_MODEL"
    assert keep_on["switch_env_var"] == ""


def test_normalize_custom_profile_rejects_invalid_switch_variable():
    """Shell environment variable names must be validated before catalog writes."""
    with pytest.raises(ValueError, match="environment variable"):
        lm.normalize_custom_profile({
            "name": "Agent Model",
            "model_key": "agent-model",
            "switch_env_var": "NOT-VALID",
        })


def test_normalize_custom_profile_validates_download_url_and_checksum():
    """Custom downloads accept HTTP(S) URLs and exact SHA-256 values only."""
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        lm.normalize_custom_profile({
            "name": "Bad URL",
            "model_key": "bad-url",
            "download_url": "file:///etc/passwd",
        })
    with pytest.raises(ValueError, match="sha256"):
        lm.normalize_custom_profile({
            "name": "Bad Hash",
            "model_key": "bad-hash",
            "download_url": "https://example.com/model.gguf",
            "sha256": "short",
        })


def test_custom_install_selection_preserves_models_and_applies_role(registry):
    """An additive custom model can become main or a switch-free keep-on model."""
    main_choice = lm.custom_install_selection(
        registry,
        {
            "name": "My Agent",
            "model_key": "my-agent",
            "download_url": "https://example.com/my-agent.gguf",
            "switch_env_var": "USE_MY_AGENT",
        },
        role="main",
    )
    main_catalog = lm.apply_install_selection(registry, **main_choice)
    assert lm.default_plan(main_catalog).main_id == "my-agent"

    keep_choice = lm.custom_install_selection(
        registry,
        {
            "name": "My Autocomplete",
            "model_key": "my-autocomplete",
            "download_url": "https://example.com/my-autocomplete.gguf",
            "switch_env_var": "",
        },
        role="keep-on",
    )
    keep_catalog = lm.apply_install_selection(registry, **keep_choice)
    keep_plan = lm.default_plan(keep_catalog)
    assert "my-autocomplete" in keep_plan.keep_on_ids
    assert keep_plan.switch_env_vars["my-autocomplete"] == ""


def test_normalize_custom_profile_requires_name():
    """A custom model with no usable name is rejected."""
    with pytest.raises(ValueError):
        lm.normalize_custom_profile({"model_key": "x"})


# --- apply_install_selection ----------------------------------------------------


def test_apply_selection_default_layout_roundtrips(registry):
    """Selecting both shipped models with autostart reproduces default_plan."""
    customized = lm.apply_install_selection(
        registry,
        selected_ids=["vibethinker", "ornstein"],
        autostart_ids=["vibethinker", "ornstein"],
    )
    plan = lm.default_plan(customized)
    assert plan.main_id == "ornstein"
    assert plan.keep_on_ids == ("vibethinker",)    # autostart minus main
    assert set(plan.install_ids) == {"vibethinker", "ornstein"}


def test_apply_selection_subset_reassigns_main(registry):
    """Dropping the default main reassigns it to the selected keep-on model."""
    customized = lm.apply_install_selection(
        registry,
        selected_ids=["vibethinker"],
        autostart_ids=["vibethinker"],
    )
    plan = lm.default_plan(customized)
    assert plan.main_id == "vibethinker"


def test_apply_selection_not_autostarted_is_not_boot_loaded(registry):
    """A selected-but-not-autostarted model is installed but not loaded at boot."""
    customized = lm.apply_install_selection(
        registry,
        selected_ids=["vibethinker", "ornstein"],
        autostart_ids=["ornstein"],
    )
    plan = lm.default_plan(customized)
    specs = lm.boot_load_specs(customized, plan, env={})
    keys = [s["model_key"] for s in specs]
    assert "vibethinker-1.5b" not in keys
    assert "ornstein3.6-35b-a3b" in keys


def test_apply_selection_adds_custom_model(registry):
    """A custom model is appended, installed, and boot-loaded when autostarted."""
    customized = lm.apply_install_selection(
        registry,
        selected_ids=["my-model"],
        autostart_ids=["my-model"],
        custom_profiles=[{
            "name": "My Model",
            "model_key": "my-model-key",
            "download_url": "https://example.com/my/My-Model.gguf",
        }],
    )
    plan = lm.default_plan(customized)
    assert plan.main_id == "my-model"
    specs = lm.download_specs(customized, None)
    assert [s["id"] for s in specs] == ["my-model"]


def test_apply_selection_rejects_unknown_and_unselected_autostart(registry):
    """Selection validates ids and that autostart is a subset of installs."""
    with pytest.raises(ValueError):
        lm.apply_install_selection(registry, selected_ids=["nope"], autostart_ids=[])
    with pytest.raises(ValueError):
        lm.apply_install_selection(
            registry, selected_ids=["vibethinker"], autostart_ids=["ornstein"]
        )


def test_apply_selection_empty_is_core_only(registry, tmp_path, capsys):
    """Selecting zero models yields an empty, non-crashing plan (core-only)."""
    customized = lm.apply_install_selection(registry, selected_ids=[], autostart_ids=[])
    assert not any(p["default_install"] for p in customized["profiles"])
    plan = lm.default_plan(customized)
    assert plan.install_ids == () and plan.main_id == ""
    assert lm.boot_load_specs(customized, plan, env={}) == []
    assert lm.download_specs(customized, None) == []
    # The CLI surfaces of the empty catalog stay silent rather than erroring.
    empty_catalog = tmp_path / "empty.json"
    empty_catalog.write_text(json.dumps(customized), encoding="utf-8")
    for cmd in ("active-main", "keep-on", "boot-load", "download-list"):
        assert lm.main([cmd, "--catalog", str(empty_catalog)]) == 0
        assert capsys.readouterr().out.strip() == ""


def test_cli_apply_selection_writes_catalog(registry, tmp_path, capsys):
    """The apply-selection CLI emits a customized catalog from a selection file."""
    selection = tmp_path / "sel.json"
    selection.write_text(json.dumps({
        "selected_ids": ["vibethinker"],
        "autostart_ids": ["vibethinker"],
    }), encoding="utf-8")
    assert lm.main([
        "apply-selection", "--catalog", str(CATALOG), "--selection", str(selection)
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    installed = [p["id"] for p in out["profiles"] if p["default_install"]]
    assert installed == ["vibethinker"]
    assert lm.default_plan(out).main_id == "vibethinker"
