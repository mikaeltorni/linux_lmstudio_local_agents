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
    """The menu opens on the shipped layout (vibethinker and Ornstein autostart)."""
    state = menu.SelectionState(profiles)
    sel = state.selection()
    assert set(sel["selected_ids"]) == {"vibethinker", "ornstein"}
    assert set(sel["autostart_ids"]) == {"vibethinker", "ornstein"}
    rendered = menu.render(state)
    assert "LMSTUDIO_USE_VIBETHINKER" in rendered
    assert "LMSTUDIO_USE_ORNSTEIN" in rendered


def test_toggle_install_clears_autostart(profiles):
    """Deselecting a model also removes it from autostart."""
    state = menu.SelectionState(profiles)
    state.toggle_install(2)  # Ornstein (installed + autostart) -> off
    sel = state.selection()
    assert "ornstein" not in sel["selected_ids"]
    assert "ornstein" not in sel["autostart_ids"]


def test_toggle_autostart_selects_model(profiles):
    """Enabling autostart on a non-autostart model also installs it."""
    state = menu.SelectionState(profiles)
    state.toggle_install(2)
    state.toggle_autostart(2)
    sel = state.selection()
    assert "ornstein" in sel["autostart_ids"]
    assert "ornstein" in sel["selected_ids"]


def test_run_menu_scripted_toggles(profiles):
    """A scripted session toggles install/autostart then installs."""
    reader = _scripted_reader(["2", "b1", ""])  # drop ornstein, autostart->off vibethinker, done
    sel = menu.run_menu(profiles, reader=reader, writer=_silent_writer)
    assert "ornstein" not in sel["selected_ids"]
    assert "vibethinker" not in sel["autostart_ids"]
    assert "vibethinker" in sel["selected_ids"]


def test_run_menu_add_custom_model(profiles):
    """The 'add' flow records a custom model, selected and autostarted."""
    reader = _scripted_reader([
        "none",                       # clear shipped selection
        "a",                          # add custom
        "Tiny Llama 1B",              # name
        "tinyllama-1b",               # model key
        "https://example.com/T.gguf", # url
        "USE_TINY_LLAMA",             # switch environment variable
        "",                            # sha256 (skip)
        "",                            # done
    ])
    sel = menu.run_menu(profiles, reader=reader, writer=_silent_writer)
    assert sel["selected_ids"] == ["tiny-llama-1b"]
    assert sel["autostart_ids"] == ["tiny-llama-1b"]
    assert sel["custom_profiles"][0]["model_key"] == "tinyllama-1b"
    assert sel["custom_profiles"][0]["switch_env_var"] == "USE_TINY_LLAMA"
    # The whole selection feeds apply_install_selection cleanly.
    customized = lm.apply_install_selection(lm.load_registry(CATALOG), **sel)
    assert lm.default_plan(customized).main_id == "tiny-llama-1b"


def test_run_menu_all_and_none(profiles):
    """'all' selects every model; a trailing toggle still applies."""
    reader = _scripted_reader(["none", "1", ""])
    sel = menu.run_menu(profiles, reader=reader, writer=_silent_writer)
    assert sel["selected_ids"] == ["vibethinker"]
