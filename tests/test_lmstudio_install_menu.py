#!/usr/bin/env python3
"""Tests for the interactive model-selection menu.

The selection state and the scripted command loop are exercised without a real
terminal by injecting a reader (scripted input lines) and a writer (capturing
output), so the per-model install/autostart toggles and the custom-model flow
are verified deterministically.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "configs" / "lmstudio" / "model-profiles.json"


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


menu = _load("lmstudio_install_menu")
lm = _load("lmstudio_models")


@pytest.fixture()
def profiles():
    return lm.profiles(lm.load_registry(CATALOG))


def _scripted_reader(lines):
    it = iter(lines)

    def reader(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    return reader


def _silent_writer(_text):
    pass


def test_initial_state_matches_shipped_defaults(profiles):
    """Qwen is installed on demand while optional VibeThinker stays deselected."""
    state = menu.SelectionState(profiles)
    sel = state.selection()
    assert sel["selected_ids"] == ["qwen3.8-27b"]
    assert sel["autostart_ids"] == []
    rendered = menu.render(state)
    assert "LMSTUDIO_USE_VIBETHINKER" in rendered
    assert "LMSTUDIO_USE_QWEN3_8_27B" in rendered


def test_toggle_install_clears_autostart(profiles):
    """Deselecting a model also removes it from autostart."""
    state = menu.SelectionState(profiles)
    state.toggle_install(2)  # Qwen3.8-27B (installed, startup-off) -> off
    sel = state.selection()
    assert "qwen3.8-27b" not in sel["selected_ids"]
    assert "qwen3.8-27b" not in sel["autostart_ids"]


def test_toggle_autostart_selects_model(profiles):
    """Enabling autostart on a non-autostart model also installs it."""
    state = menu.SelectionState(profiles)
    state.toggle_install(2)
    state.toggle_autostart(2)
    sel = state.selection()
    assert "qwen3.8-27b" in sel["autostart_ids"]
    assert "qwen3.8-27b" in sel["selected_ids"]


def test_run_menu_scripted_toggles(profiles):
    """A scripted session toggles install/autostart then installs."""
    reader = _scripted_reader(["1", "2", "b1", ""])  # add VibeThinker, drop Qwen, autostart VibeThinker
    sel = menu.run_menu(profiles, reader=reader, writer=_silent_writer)
    assert "qwen3.8-27b" not in sel["selected_ids"]
    assert "vibethinker" in sel["autostart_ids"]
    assert "vibethinker" in sel["selected_ids"]


def test_run_menu_add_custom_model(profiles):
    """A accepts one source line; E/G/S customize the resulting model row."""
    reader = _scripted_reader([
        "none",
        "a",
        "https://huggingface.co/acme/Tiny-Llama-GGUF@Q4_K_M",
        "e3", "USE_TINY_LLAMA",
        "g3", "2",
        "s3",
        "",
    ])
    sel = menu.run_menu(profiles, reader=reader, writer=_silent_writer)
    assert sel["selected_ids"] == ["tiny-llama-gguf"]
    assert sel["autostart_ids"] == []
    assert sel["custom_profiles"][0]["model_key"] == "acme/Tiny-Llama-GGUF@Q4_K_M"
    assert sel["custom_profiles"][0]["switch_env_var"] == "USE_TINY_LLAMA"
    assert sel["custom_profiles"][0]["group"] == 2
    # The whole selection feeds apply_install_selection cleanly.
    customized = lm.apply_install_selection(lm.load_registry(CATALOG), **sel)
    assert lm.default_plan(customized).main_id == "tiny-llama-gguf"


def test_a_can_add_multiple_source_rows(profiles):
    reader = _scripted_reader([
        "none", "a", "owner/one@q4_k_m", "a", "owner/two@q5_k_m", "",
    ])
    sel = menu.run_menu(profiles, reader=reader, writer=_silent_writer)
    assert sel["selected_ids"] == ["one", "two"]
    assert len(sel["custom_profiles"]) == 2


def test_run_menu_all_and_none(profiles):
    """'all' selects every model; a trailing toggle still applies."""
    reader = _scripted_reader(["none", "1", ""])
    sel = menu.run_menu(profiles, reader=reader, writer=_silent_writer)
    assert sel["selected_ids"] == ["vibethinker"]
