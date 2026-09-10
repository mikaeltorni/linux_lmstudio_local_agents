#!/usr/bin/env python3
"""Tests for the LM Studio model registry and resolution logic.

These exercise the pure planning/resolution helpers against the real registry in
configs/lmstudio/model-profiles.json, so the shipped default layout (VibeThinker
retained as optional and Qwen3.8-27B main) is locked in, plus the env-var switching
precedence and the validation rules.
"""

from __future__ import annotations

import importlib.util
import copy
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
    assert lm.find_profile(registry, "qwen3.8-27b")["id"] == "qwen3.8-27b"
    assert lm.find_profile(registry, "nope") is None


def test_default_plan_matches_shipped_layout(registry):
    """The default plan encodes the user's required default model layout."""
    plan = lm.default_plan(registry)
    assert plan.install_ids == ("qwen3.8-27b",)
    assert plan.keep_on_ids == ()
    assert plan.main_id == "qwen3.8-27b"
    assert plan.main_env_var == "LMSTUDIO_MAIN_MODEL"
    assert plan.switch_env_vars == {
        "qwen3.8-27b": "LMSTUDIO_USE_QWEN3_8_27B",
    }


def test_default_plan_keep_on_model_keys(registry):
    """The shipped default keeps no model resident permanently."""
    plan = lm.default_plan(registry)
    assert plan.keep_on_model_keys(registry) == []


def test_resolve_active_main_defaults_to_qwen3_8_27b(registry):
    """With no switch variables set, the active main model is Qwen3.8-27B."""
    plan = lm.default_plan(registry)
    assert lm.resolve_active_main(registry, plan, env={})["model_key"] == "qwen3.8-27b"


def test_resolve_active_main_switch_var_wins(registry):
    """A truthy per-model switch variable switches the active main model."""
    plan = lm.build_plan(
        registry,
        install_ids=["vibethinker", "qwen3.8-27b"],
        keep_on_ids=[],
        main_id="qwen3.8-27b",
    )
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_VIBETHINKER": "1"})
    assert active["model_key"] == "vibethinker-1.5b"


def test_resolve_active_main_switch_var_precedence_is_registry_order(registry):
    """When multiple switch vars are set, the first in registry order wins."""
    plan = lm.build_plan(
        registry,
        install_ids=["vibethinker", "qwen3.8-27b"],
        keep_on_ids=[],
        main_id="qwen3.8-27b",
    )
    active = lm.resolve_active_main(
        registry, plan, env={"LMSTUDIO_USE_VIBETHINKER": "yes", "LMSTUDIO_USE_QWEN3_8_27B": "on"}
    )
    assert active["model_key"] == "vibethinker-1.5b"


def test_resolve_active_main_main_env_var_names_model(registry):
    """LMSTUDIO_MAIN_MODEL selects an installed model when no switch var is set."""
    plan = lm.build_plan(
        registry,
        install_ids=["vibethinker", "qwen3.8-27b"],
        keep_on_ids=[],
        main_id="qwen3.8-27b",
    )
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_MAIN_MODEL": "vibethinker"})
    assert active["model_key"] == "vibethinker-1.5b"


def test_resolve_active_main_switch_var_beats_main_env_var(registry):
    """A per-model switch var takes precedence over LMSTUDIO_MAIN_MODEL."""
    plan = lm.default_plan(registry)
    active = lm.resolve_active_main(
        registry, plan, env={"LMSTUDIO_MAIN_MODEL": "vibethinker", "LMSTUDIO_USE_QWEN3_8_27B": "1"}
    )
    assert active["model_key"] == "qwen3.8-27b"


def test_resolve_active_main_ignores_uninstalled_main_env_target(registry):
    """An LMSTUDIO_MAIN_MODEL that names an uninstalled model is ignored."""
    plan = lm.build_plan(
        registry, install_ids=["qwen3.8-27b"], keep_on_ids=[], main_id="qwen3.8-27b"
    )
    active = lm.resolve_active_main(registry, plan, env={"LMSTUDIO_MAIN_MODEL": "vibethinker"})
    assert active["model_key"] == "qwen3.8-27b"


def test_falsey_switch_var_does_not_switch(registry):
    """A non-truthy switch value does not change the active model."""
    plan = lm.default_plan(registry)
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_VIBETHINKER": "0"})["model_key"] == "qwen3.8-27b"
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_USE_VIBETHINKER": ""})["model_key"] == "qwen3.8-27b"


def test_build_plan_rejects_empty_install(registry):
    """An empty install set is rejected."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=[], keep_on_ids=[], main_id="qwen3.8-27b")


def test_build_plan_rejects_unknown_id(registry):
    """Unknown model ids are rejected."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["mystery"], keep_on_ids=[], main_id="mystery")


def test_build_plan_rejects_keep_on_not_installed(registry):
    """Keep-on models must be part of the install set."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["qwen3.8-27b"], keep_on_ids=["vibethinker"], main_id="qwen3.8-27b")


def test_build_plan_rejects_main_not_installed(registry):
    """The main model must be part of the install set."""
    with pytest.raises(ValueError):
        lm.build_plan(registry, install_ids=["vibethinker"], keep_on_ids=["vibethinker"], main_id="qwen3.8-27b")


def test_build_plan_switch_var_override(registry):
    """Explicit switch-var overrides win over the registry defaults."""
    plan = lm.build_plan(
        registry,
        install_ids=["vibethinker", "qwen3.8-27b"],
        keep_on_ids=[],
        main_id="qwen3.8-27b",
        switch_env_vars={"vibethinker": "LMSTUDIO_BACKUP"},
    )
    assert plan.switch_env_vars["vibethinker"] == "LMSTUDIO_BACKUP"
    assert lm.resolve_active_main(registry, plan, env={"LMSTUDIO_BACKUP": "1"})["model_key"] == "vibethinker-1.5b"


def test_cli_active_main_and_keep_on(registry, capsys):
    """The CLI prints the default active main and an empty keep-on set."""
    assert lm.main(["active-main", "--catalog", str(CATALOG)]) == 0
    assert capsys.readouterr().out.strip() == "qwen3.8-27b"
    assert lm.main(["keep-on", "--catalog", str(CATALOG)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_cli_boot_load_serializes_booleans_for_shell_cases(capsys, monkeypatch):
    """Startup Qwen records use lowercase booleans accepted by the shell loader."""
    monkeypatch.setenv("LMSTUDIO_USE_QWEN3_8_27B", "1")
    assert lm.main(["boot-load", "--catalog", str(CATALOG)]) == 0
    records = capsys.readouterr().out.strip().splitlines()
    qwen = records[-1].split(lm.BOOT_LOAD_FIELD_SEPARATOR)
    values = dict(zip(lm.BOOT_LOAD_FIELDS, qwen, strict=True))
    assert values["speculative_draft_mtp"] == "true"
    assert values["flash_attention"] == "true"
    assert values["offload_kv_cache_to_gpu"] == "true"
    assert values["k_cache_quantization_type"] == "q8_0"
    assert values["v_cache_quantization_type"] == "q8_0"
    assert values["eval_batch_size"] == "2048"
    assert values["context_length"] == "98304"


def test_cli_default_plan_json(capsys):
    """The default-plan subcommand emits valid JSON describing the plan."""
    assert lm.main(["default-plan", "--catalog", str(CATALOG)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["main_id"] == "qwen3.8-27b"
    assert payload["keep_on_ids"] == []


def test_upgrade_catalog_applies_new_startup_policy_and_preserves_other_choices(registry):
    """A new startup policy enables Qwen while preserving other choices."""
    installed = copy.deepcopy(registry)
    installed["schema_version"] = 5
    qwen = lm.find_profile(installed, "qwen3.8-27b")
    qwen.update({
        "model_path": "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf",
        "context_length": 40960,
        "switch_env_var": "MY_QWEN_SWITCH",
        "default_startup": False,
        "group": 7,
    })
    custom = lm.normalize_custom_profile({
        "name": "Private Coder",
        "model_key": "private-coder",
        "download_url": "https://example.com/private-coder.gguf",
    })
    installed["profiles"].append(custom)

    upgraded = lm.upgrade_catalog(installed, registry)

    upgraded_qwen = lm.find_profile(upgraded, "qwen3.8-27b")
    assert upgraded["schema_version"] == 14
    assert upgraded_qwen["model_path"].endswith("UD-Q3_K_XL.gguf")
    assert upgraded_qwen["context_length"] == 98304
    assert upgraded_qwen["offload_kv_cache_to_gpu"] is True
    assert upgraded_qwen["k_cache_quantization_type"] == "q8_0"
    assert upgraded_qwen["switch_env_var"] == "MY_QWEN_SWITCH"
    assert upgraded_qwen["default_startup"] is True
    assert upgraded_qwen["ttl"] == 0
    assert upgraded_qwen["group"] == 7
    assert lm.find_profile(upgraded, custom["id"])["custom"] is True


def test_upgrade_catalog_preserves_startup_choice_after_policy_schema(registry):
    """Later schema upgrades preserve explicit startup choices."""
    installed = copy.deepcopy(registry)
    installed["profiles"][1]["default_startup"] = True
    shipped = copy.deepcopy(registry)
    shipped["schema_version"] = 15

    upgraded = lm.upgrade_catalog(installed, shipped)

    assert upgraded["schema_version"] == 15
    assert lm.find_profile(upgraded, "qwen3.8-27b")["default_startup"] is True


def test_boot_load_specs_default_order(registry):
    """The shipped Qwen default is emitted for boot loading."""
    plan = lm.default_plan(registry)
    specs = lm.boot_load_specs(registry, plan, env={})
    assert [spec["model_key"] for spec in specs] == ["qwen3.8-27b"]


def test_boot_load_specs_follows_switch_var(registry):
    """A switch var loads the on-demand main model without a keep-on peer."""
    plan = lm.default_plan(registry)
    specs = lm.boot_load_specs(registry, plan, env={"LMSTUDIO_USE_QWEN3_8_27B": "1"})
    assert [spec["model_key"] for spec in specs] == ["qwen3.8-27b"]
    assert specs[0]["gpu"] == "max"
    assert specs[0]["context_length"] == 98304
    assert specs[0]["load_key"] == "qwen3.8-27b@q3_k_xl"
    assert specs[0]["model_path"].endswith("Qwen3.8-27B-UD-Q3_K_XL.gguf")
    assert specs[0]["ttl"] == 0
    assert specs[0]["speculative_draft_mtp"] is True
    assert specs[0]["k_cache_quantization_type"] == "q8_0"
    assert specs[0]["v_cache_quantization_type"] == "q8_0"
    assert specs[0]["context_checkpoints"] == 4
    assert specs[0]["eval_batch_size"] == 2048
    assert specs[0]["physical_batch_size"] == 512
    assert specs[0]["flash_attention"] is True
    assert specs[0]["offload_kv_cache_to_gpu"] is True


def test_boot_load_specs_no_duplicate_when_main_is_keep_on(registry):
    """If the active main is also a keep-on model it is not loaded twice."""
    registry["profiles"][0]["default_startup"] = True
    plan = lm.build_plan(
        registry, install_ids=["vibethinker"], keep_on_ids=["vibethinker"], main_id="vibethinker"
    )
    specs = lm.boot_load_specs(registry, plan, env={})
    assert [spec["model_key"] for spec in specs] == ["vibethinker-1.5b"]


def test_installable_ids_default_is_all_default_install(registry):
    """With no selection, every default_install model is installable."""
    assert lm.installable_ids(registry) == ["qwen3.8-27b"]


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
    assert lines[0].split(lm.DOWNLOAD_FIELD_SEPARATOR)[0] == "vibethinker"


def test_cli_download_list_default_lists_all(registry, capsys, monkeypatch):
    """Without the env var, download-list lists every default_install model."""
    monkeypatch.delenv("LMSTUDIO_INSTALL_MODELS", raising=False)
    assert lm.main(["download-list", "--catalog", str(CATALOG)]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    ids = {line.split(lm.DOWNLOAD_FIELD_SEPARATOR)[0] for line in lines}
    assert ids == {"qwen3.8-27b"}


def test_download_list_preserves_empty_catalog_source(registry, capsys, monkeypatch):
    """Direct URLs retain an empty source field for the shell downloaders."""
    monkeypatch.delenv("LMSTUDIO_INSTALL_MODELS", raising=False)
    assert lm.main(["download-list", "--catalog", str(CATALOG)]) == 0
    fields = capsys.readouterr().out.splitlines()[0].split(lm.DOWNLOAD_FIELD_SEPARATOR)
    assert len(fields) == 5
    assert fields[1].startswith("https://")
    assert fields[2] == ""


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


def test_normalize_custom_source_accepts_hugging_face_url_and_lms_spec():
    url = lm.custom_profile_from_source(
        "https://huggingface.co/acme/Tiny-Llama-GGUF@Q4_K_M"
    )
    assert url["id"] == "tiny-llama-gguf"
    assert url["model_key"] == "acme/Tiny-Llama-GGUF@Q4_K_M"
    assert url["download_source"] == "acme/Tiny-Llama-GGUF@Q4_K_M"
    spec = lm.custom_profile_from_source("acme/Other-GGUF@q5_k_m")
    assert spec["id"] == "other-gguf"
    assert spec["download_source"] == "acme/Other-GGUF@q5_k_m"


def test_hugging_face_repository_url_can_use_lm_studio_default_quantization():
    profile = lm.custom_profile_from_source("https://huggingface.co/acme/Tiny-Llama-GGUF")
    assert profile["download_source"] == "https://huggingface.co/acme/Tiny-Llama-GGUF"
    assert profile["model_key"] == "acme/Tiny-Llama-GGUF"


def test_exact_hugging_face_gguf_file_keeps_resumable_direct_download():
    profile = lm.custom_profile_from_source(
        "https://huggingface.co/acme/model-GGUF/resolve/main/model-Q4_K_M.gguf"
    )
    assert profile["download_url"].endswith("model-Q4_K_M.gguf")
    assert "download_source" not in profile


def test_grouped_env_selection_replaces_startup_peer_but_ungrouped_is_additive(registry):
    registry["profiles"][0]["default_install"] = True
    registry["profiles"][0]["group"] = 1
    registry["profiles"][1]["group"] = 1
    registry["profiles"][0]["default_startup"] = True
    registry["profiles"][1]["default_startup"] = False
    plan = lm.default_plan(registry)
    grouped = lm.boot_load_specs(registry, plan, env={"LMSTUDIO_USE_QWEN3_8_27B": "1"})
    assert [item["model_key"] for item in grouped] == ["qwen3.8-27b"]
    registry["profiles"][1]["group"] = 0
    additive = lm.boot_load_specs(registry, plan, env={"LMSTUDIO_USE_QWEN3_8_27B": "1"})
    assert [item["model_key"] for item in additive] == [
        "vibethinker-1.5b", "qwen3.8-27b"
    ]


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
        selected_ids=["vibethinker", "qwen3.8-27b"],
        autostart_ids=["vibethinker", "qwen3.8-27b"],
    )
    plan = lm.default_plan(customized)
    assert plan.main_id == "qwen3.8-27b"
    assert plan.keep_on_ids == ("vibethinker",)    # autostart minus main
    assert set(plan.install_ids) == {"vibethinker", "qwen3.8-27b"}


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
        selected_ids=["vibethinker", "qwen3.8-27b"],
        autostart_ids=["qwen3.8-27b"],
    )
    plan = lm.default_plan(customized)
    specs = lm.boot_load_specs(customized, plan, env={})
    keys = [s["model_key"] for s in specs]
    assert "vibethinker-1.5b" not in keys
    assert "qwen3.8-27b" in keys


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
            registry, selected_ids=["vibethinker"], autostart_ids=["qwen3.8-27b"]
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
