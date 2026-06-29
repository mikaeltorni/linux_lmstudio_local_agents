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


def _profile(profiles, pid):
    """Return the profile with the given id."""
    return next(profile for profile in profiles if profile["id"] == pid)


def test_catalog_contains_expected_model_commands():
    """The catalog defines only the vibethinker and Ornstein profiles."""
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    profiles = {profile["id"]: profile for profile in catalog["profiles"]}
    assert set(profiles) == {"vibethinker", "ornstein"}
    assert profiles["ornstein"]["command"] == "ornstein"
    assert profiles["ornstein"]["model_key"] == "ornstein3.6-35b-a3b"
    assert profiles["vibethinker"]["model_key"] == "vibethinker-1.5b"
    assert profiles["vibethinker"]["default_keep_on"] is True


def test_restore_uses_persisted_profile_then_boot_default():
    """Restore uses the persisted profile, falling back to the boot default."""
    profiles = MOD.load_profiles(CATALOG)
    with tempfile.TemporaryDirectory() as td:
        state_file = Path(td) / "selected-model"
        assert MOD.selected_profile(profiles, None, state_file)["id"] == "ornstein"
        state_file.write_text("ornstein\n", encoding="utf-8")
        assert MOD.selected_profile(profiles, None, state_file)["id"] == "ornstein"


def test_switchable_profiles_excludes_keep_on():
    """The switchable set excludes keep-on models (the VibeThinker model)."""
    profiles = MOD.load_profiles(CATALOG)
    ids = {profile["id"] for profile in MOD.switchable_profiles(profiles)}
    assert ids == {"ornstein"}


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
    MOD.switch_model(_profile(profiles, "ornstein"), profiles, Path("/fake/lms"))
    assert "vibethinker-1.5b" not in unloaded
    assert set(unloaded) == {"ornstein3.6-35b-a3b"}


def test_failed_switch_restores_previous_model(monkeypatch):
    """A failed target load restores the previously loaded switchable model."""
    profiles = MOD.load_profiles(CATALOG)
    alternate = copy.deepcopy(_profile(profiles, "ornstein"))
    alternate.update({
        "id": "alternate-coder",
        "command": "alternate",
        "model_key": "alternate-coder-key",
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
            # switch_model first call: the alternate is loaded, Ornstein is not
            if len([c for c in calls if c[:2] == ("ps", "--json")]) == 1:
                return MOD.subprocess.CompletedProcess(args, 0, '[{"identifier":"alternate-coder-key"}]', "")
            # load_model pre-check: ornstein is not loaded yet
            return MOD.subprocess.CompletedProcess(args, 0, "[]", "")
        if args[0] == "load" and "ornstein3.6-35b-a3b" in args:
            return MOD.subprocess.CompletedProcess(args, 1, "", "load failed\n")
        if args[0] == "load" and "alternate-coder-key" in args:
            return MOD.subprocess.CompletedProcess(args, 0, "", "")
        return MOD.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(MOD, "run_lms", fake_run)
    try:
        MOD.switch_model(_profile(profiles, "ornstein"), profiles, Path("/fake/lms"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed target load should raise")

    load_calls = [call for call in calls if call[0] == "load"]
    assert len(load_calls) >= 2
    assert "ornstein3.6-35b-a3b" in load_calls[0]
    assert "alternate-coder-key" in load_calls[1]


def test_load_model_unloads_before_loading_when_model_present(monkeypatch):
    """load_model waits for unload to complete before loading."""
    profile = {
        "id": "ornstein",
        "model_key": "ornstein3.6-35b-a3b",
        "model_path": "GestaltLabs/Ornstein3.6-35B-A3B-GGUF/Ornstein3.6-35B-A3B-Q3_K_M.gguf",
        "context_length": 131072,
        "gpu": "max",
        "parallel": 1,
        "ttl": 0,
    }
    calls = []

    def fake_run(_lms, *args, check=True):
        """Provide a test double for run."""
        calls.append(args)
        if args[:2] == ("ls", "--json"):
            return MOD.subprocess.CompletedProcess(
                args,
                0,
                json.dumps([{"modelKey": "ornstein3.6-35b-a3b-q3_k_m", "path": profile["model_path"]}]),
                "",
            )
        if args[:2] == ("ps", "--json"):
            # First call shows model loaded (pre-check), then subsequent show unloaded
            if len([c for c in calls if c[:1] == ("ps", "--json")]) == 1:
                return MOD.subprocess.CompletedProcess(
                    args, 0, json.dumps([{"identifier": "ornstein3.6-35b-a3b"}]), ""
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
        "id": "ornstein",
        "model_key": "ornstein3.6-35b-a3b",
        "model_path": "GestaltLabs/Ornstein3.6-35B-A3B-GGUF/Ornstein3.6-35B-A3B-Q3_K_M.gguf",
        "context_length": 131072,
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
                args, 0, json.dumps([{"identifier": "ornstein3.6-35b-a3b"}]), ""
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
        "id": "ornstein",
        "model_key": "ornstein3.6-35b-a3b",
        "model_path": "GestaltLabs/Ornstein3.6-35B-A3B-GGUF/Ornstein3.6-35B-A3B-Q3_K_M.gguf",
        "context_length": 131072,
        "gpu": "max",
        "parallel": 1,
        "ttl": 0,
    }
    calls = []

    def fake_run(_lms, *args, check=True):
        """Provide a test double for run."""
        calls.append(args)
        if args[:2] == ("ls", "--json"):
            return MOD.subprocess.CompletedProcess(
                args,
                0,
                json.dumps([{"modelKey": "ornstein3.6-35b-a3b-q3_k_m", "path": profile["model_path"]}]),
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
    assert load_call[load_call.index("--identifier") + 1] == "ornstein3.6-35b-a3b"
    assert "ornstein3.6-35b-a3b-q3_k_m" in load_call


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
        MOD.switch_model(_profile(profiles, "ornstein"), profiles, Path("/fake/lms"))

    messages = [record.getMessage() for record in caplog.records]
    assert "Starting LM Studio server if needed." in messages
    assert "Loading LM Studio model: ornstein3.6-35b-a3b" in messages
    assert "Active LM Studio model: ornstein3.6-35b-a3b" in messages


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
        MOD.switch_model(_profile(profiles, "ornstein"), profiles, Path("/fake/lms"))

    messages = [record.getMessage() for record in caplog.records]
    assert "Could not parse `lms ps --json`; previous model restore will be unavailable." in messages
    assert "Could not parse `lms ls --json`; using configured model key." in messages
