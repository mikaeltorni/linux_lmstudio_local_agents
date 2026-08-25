#!/usr/bin/env python3
"""Tests for persistent catalog-driven LM Studio model selection.

Ported from agent_command_center, where this logic used to live, and extended for
the keep-on aware switcher: switching the main slot must not unload keep-on
models (the CPU-resident VibeThinker).
"""

from __future__ import annotations

import importlib.util
import copy
import json
import logging
import sys
from pathlib import Path
import tempfile
import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "lmstudio_model_selector", SCRIPTS_DIR / "lmstudio_model_selector.py"
)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)

CATALOG = ROOT_DIR / "configs/lmstudio/model-profiles.json"


@pytest.fixture(autouse=True)
def _stub_advanced_loader(monkeypatch):
    """Keep unit tests local while advanced-loader behavior has a focused test."""
    original = MOD.subprocess.run

    def run(args, **kwargs):
        if any("lmstudio-sdk-loader" in str(arg) or "lmstudio_sdk_loader" in str(arg) for arg in args):
            return MOD.subprocess.CompletedProcess(args, 0, "", "")
        return original(args, **kwargs)

    monkeypatch.setattr(MOD.subprocess, "run", run)


def _profile(profiles, pid):
    """Return the profile with the given id."""
    return next(profile for profile in profiles if profile["id"] == pid)


def test_catalog_contains_expected_model_commands():
    """The catalog defines only the VibeThinker and Qwen3.8-27B profiles."""
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    profiles = {profile["id"]: profile for profile in catalog["profiles"]}
    assert set(profiles) == {"vibethinker", "qwen3.8-27b"}
    assert profiles["qwen3.8-27b"]["command"] == "qwen3.8-27b"
    qwen = profiles["qwen3.8-27b"]
    assert qwen["model_key"] == "qwen3.8-27b"
    assert qwen["load_key"] == "qwen3.8-27b@q3_k_xl"
    assert qwen["model_path"].endswith("Qwen3.8-27B-UD-Q3_K_XL.gguf")
    assert qwen["context_length"] == 131072
    assert qwen["k_cache_quantization_type"] == "q8_0"
    assert qwen["v_cache_quantization_type"] == "q8_0"
    assert qwen["context_checkpoints"] == 4
    assert profiles["qwen3.8-27b"]["default_startup"] is True
    assert profiles["vibethinker"]["model_key"] == "vibethinker-1.5b"
    assert profiles["vibethinker"]["default_keep_on"] is True


def test_restore_uses_persisted_profile_then_boot_default():
    """Restore uses the persisted profile, falling back to the boot default."""
    profiles = MOD.load_profiles(CATALOG)
    with tempfile.TemporaryDirectory() as td:
        state_file = Path(td) / "selected-model"
        assert MOD.selected_profile(profiles, None, state_file)["id"] == "qwen3.8-27b"
        state_file.write_text("qwen3.8-27b\n", encoding="utf-8")
        assert MOD.selected_profile(profiles, None, state_file)["id"] == "qwen3.8-27b"


def test_switchable_profiles_excludes_keep_on():
    """The switchable set excludes keep-on models (the VibeThinker model)."""
    profiles = MOD.load_profiles(CATALOG)
    ids = {profile["id"] for profile in MOD.switchable_profiles(profiles)}
    assert ids == {"qwen3.8-27b"}


def test_switch_model_does_not_unload_keep_on(monkeypatch):
    """Switching the main slot never unloads the keep-on VibeThinker."""
    profiles = MOD.load_profiles(CATALOG)
    unloaded = []

    def fake_run(_lms, *args, check=True):
        """Record unloads and stub everything else as success."""
        if args[:1] == ("unload",):
            unloaded.append(args[1])
        if args[:2] == ("ls", "--json"):
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        if args[:2] == ("ps", "--json"):
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    def fake_sleep(_):
        pass

    monkeypatch.setattr(MOD, "run_lms", fake_run)
    monkeypatch.setattr(MOD, "time", type("time", (), {"sleep": staticmethod(fake_sleep)})())
    MOD.switch_model(_profile(profiles, "qwen3.8-27b"), profiles, Path("/fake/lms"))
    assert "vibethinker-1.5b" not in unloaded
    assert set(unloaded) == {"qwen3.8-27b"}


def test_failed_switch_restores_previous_model(monkeypatch):
    """A failed target load restores the previously loaded switchable model."""
    profiles = MOD.load_profiles(CATALOG)
    for field in (
        "k_cache_quantization_type", "v_cache_quantization_type",
        "context_checkpoints", "eval_batch_size", "physical_batch_size",
    ):
        profiles[1].pop(field, None)
    alternate = copy.deepcopy(_profile(profiles, "qwen3.8-27b"))
    alternate.update({
        "id": "alternate-coder",
        "command": "alternate",
        "model_key": "alternate-coder-key",
        "load_key": "alternate-coder-key",
        "model_path": "example/alternate-coder.gguf",
        "default_main": False,
    })
    profiles.append(alternate)
    calls = []

    def fake_run(_lms, *args, check=True):
        """Provide a test double for run."""
        calls.append(args)
        if args[:2] == ("ls", "--json"):
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        if args[:2] == ("ps", "--json"):
            # switch_model first call: the alternate is loaded, Qwen3.8-27B is not
            if len([c for c in calls if c[:2] == ("ps", "--json")]) == 1:
                return MOD.subprocess.CompletedProcess(args, 0, '[{"identifier":"alternate-coder-key"}]', "")
            # load_model pre-check: qwen3.8-27b is not loaded yet
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        if args[0] == "load" and "qwen3.8-27b" in args:
            return MOD.subprocess.CompletedProcess(args, 1, "", "load failed\n")
        if args[0] == "load" and "alternate-coder-key" in args:
            return MOD.subprocess.CompletedProcess(args, 0, "", "")
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(MOD, "run_lms", fake_run)
    try:
        MOD.switch_model(_profile(profiles, "qwen3.8-27b"), profiles, Path("/fake/lms"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed target load should raise")

    load_calls = [call for call in calls if call[0] == "load"]
    assert len(load_calls) >= 2
    assert "qwen3.8-27b" in load_calls[0]
    assert "alternate-coder-key" in load_calls[1]


def test_load_model_unloads_before_loading_when_model_present(monkeypatch):
    """load_model waits for unload to complete before loading."""
    profile = {
        "id": "qwen3.8-27b",
        "model_key": "qwen3.8-27b",
        "model_path": "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf",
        "context_length": 40960,
        "gpu": "max",
        "parallel": 1,
        "ttl": 0,
        "speculative_draft_mtp": True,
    }
    calls = []

    def fake_run(_lms, *args, check=True):
        """Provide a test double for run."""
        calls.append(args)
        if args[:2] == ("ls", "--json"):
            return MOD.subprocess.CompletedProcess(
                args,
                0,
                json.dumps([{"modelKey": "qwen3.8-27b-ud-q4_k_xl", "path": profile["model_path"]}]),
                "",
            )
        if args[:2] == ("ps", "--json"):
            # First call shows model loaded (pre-check), then subsequent show unloaded
            if len([c for c in calls if c[:1] == ("ps", "--json")]) == 1:
                return MOD.subprocess.CompletedProcess(
                    args, 0, json.dumps([{"identifier": "qwen3.8-27b"}]), ""
                )
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        if args[:1] == ("unload",):
            return MOD.subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "load":
            return MOD.subprocess.CompletedProcess(args, 1, "", "load failed\n")
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    def fake_sleep(_):
        pass

    monkeypatch.setattr(MOD, "run_lms", fake_run)
    monkeypatch.setattr(MOD, "time", type("time", (), {"sleep": staticmethod(fake_sleep)})())
    result = MOD.load_model(profile, Path("/fake/lms"))
    assert result.returncode == 1  # Because we fake failure

    # Verify unload was called before load since model was detected as loaded
    unload_calls = [c for c in calls if c[:1] == ("unload",)]
    load_calls = [c for c in calls if c[:1] == ("load",)]
    assert len(unload_calls) >= 2  # Both identifier and model_key
    assert len(load_calls) >= 1


def test_load_model_skips_when_already_loaded(monkeypatch):
    """load_model returns early if model is already loaded."""
    profile = {
        "id": "qwen3.8-27b",
        "model_key": "qwen3.8-27b",
        "model_path": "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf",
        "context_length": 40960,
        "gpu": "max",
        "parallel": 1,
        "ttl": 0,
    }
    calls = []

    def fake_run(_lms, *args, check=True):
        """Provide a test double for run."""
        calls.append(args)
        if args[:2] == ("ps", "--json"):
            return MOD.subprocess.CompletedProcess(
                args, 0, json.dumps([{"identifier": "qwen3.8-27b"}]), ""
            )
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(MOD, "run_lms", fake_run)
    result = MOD.load_model(profile, Path("/fake/lms"))
    assert result.returncode == 0

    # Verify load was NOT called since model was already present
    load_calls = [c for c in calls if c[:1] == ("load",)]
    assert len(load_calls) == 0


def test_load_model_resolves_local_model_key_from_catalog_path(monkeypatch):
    """load uses the local key while preserving the configured API id."""
    profile = {
        "id": "qwen3.8-27b",
        "model_key": "qwen3.8-27b",
        "model_path": "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf",
        "context_length": 40960,
        "gpu": "max",
        "parallel": 1,
        "ttl": 0,
        "speculative_draft_mtp": True,
    }
    calls = []

    def fake_run(_lms, *args, check=True):
        """Provide a test double for run."""
        calls.append(args)
        if args[:2] == ("ls", "--json"):
            return MOD.subprocess.CompletedProcess(
                args,
                0,
                json.dumps([{"modelKey": "qwen3.8-27b-ud-q4_k_xl", "path": profile["model_path"]}]),
                "",
            )
        if args[:2] == ("ps", "--json"):
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    def fake_sleep(_):
        pass

    monkeypatch.setattr(MOD, "run_lms", fake_run)
    monkeypatch.setattr(MOD, "time", type("time", (), {"sleep": staticmethod(fake_sleep)})())
    MOD.load_model(profile, Path("/fake/lms"))
    load_call = next(call for call in calls if call[0] == "load")
    assert "--identifier" in load_call
    assert load_call[load_call.index("--identifier") + 1] == "qwen3.8-27b"
    assert "--speculative-draft-mtp" in load_call
    assert "qwen3.8-27b-ud-q4_k_xl" in load_call


def test_load_model_uses_sdk_loader_for_advanced_profile(monkeypatch, tmp_path):
    """Advanced KV/checkpoint settings use the SDK loader unavailable in lms."""
    profile = _profile(MOD.load_profiles(CATALOG), "qwen3.8-27b")
    calls = []

    def fake_run_lms(_lms, *args, check=True):
        if args[:2] == ("ls", "--json"):
            item = {"modelKey": "qwen3.8-27b@q3_k_xl", "path": profile["model_path"]}
            return MOD.subprocess.CompletedProcess(args, 0, json.dumps([item]), "")
        if args[:2] == ("ps", "--json"):
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    loader = tmp_path / "lmstudio-sdk-loader.mjs"
    monkeypatch.setenv("LMSTUDIO_SDK_LOADER", str(loader))
    monkeypatch.setenv("NODE_BIN", "/fake/node")
    monkeypatch.setattr(MOD, "run_lms", fake_run_lms)
    monkeypatch.setattr(MOD.subprocess, "run", fake_run)

    result = MOD.load_model(profile, Path("/fake/lms"))

    assert result.returncode == 0
    args = calls[-1][0]
    assert args[:2] == ["/fake/node", str(loader)]
    assert args[args.index("--model") + 1] == "qwen3.8-27b@q3_k_xl"
    assert args[args.index("--context-length") + 1] == "131072"
    assert args[args.index("--k-cache") + 1] == "q8_0"
    assert args[args.index("--v-cache") + 1] == "q8_0"
    assert args[args.index("--context-checkpoints") + 1] == "4"
    assert "--mtp" in args


def test_switch_model_logs_state_transitions(monkeypatch, caplog):
    """Switching records the externally visible workloop transitions."""
    profiles = MOD.load_profiles(CATALOG)

    def fake_run(_lms, *args, check=True):
        """Stub all lms commands as successful."""
        if args[:2] in {("ls", "--json"), ("ps", "--json")}:
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    def fake_sleep(_):
        pass

    monkeypatch.setattr(MOD, "run_lms", fake_run)
    monkeypatch.setattr(MOD, "time", type("time", (), {"sleep": staticmethod(fake_sleep)})())
    monkeypatch.setattr(MOD.LOGGER, "propagate", True)
    with caplog.at_level(logging.INFO, logger="lmstudio_model_selector"):
        MOD.switch_model(_profile(profiles, "qwen3.8-27b"), profiles, Path("/fake/lms"))

    messages = [record.getMessage() for record in caplog.records]
    assert "Starting LM Studio server if needed." in messages
    assert "Loading LM Studio model: qwen3.8-27b" in messages
    assert "Active LM Studio model: qwen3.8-27b" in messages


def test_json_parse_failures_are_logged(monkeypatch, caplog):
    """Invalid lms JSON is observable while preserving fallback behavior."""
    profiles = MOD.load_profiles(CATALOG)

    def fake_run(_lms, *args, check=True):
        """Return invalid JSON for lms inventory commands."""
        if args[:2] in {("ls", "--json"), ("ps", "--json")}:
            return MOD.subprocess.CompletedProcess(args, 0, "not-json", "")
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    def fake_sleep(_):
        pass

    monkeypatch.setattr(MOD, "run_lms", fake_run)
    monkeypatch.setattr(MOD, "time", type("time", (), {"sleep": staticmethod(fake_sleep)})())
    monkeypatch.setattr(MOD.LOGGER, "propagate", True)
    with caplog.at_level(logging.WARNING, logger="lmstudio_model_selector"):
        MOD.switch_model(_profile(profiles, "qwen3.8-27b"), profiles, Path("/fake/lms"))

    messages = [record.getMessage() for record in caplog.records]
    assert "Could not parse `lms ps --json`; previous model restore will be unavailable." in messages
    assert "Could not parse `lms ls --json`; using configured model key." in messages
