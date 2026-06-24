#!/usr/bin/env python3
"""Interactive model-selection menu for the LM Studio local-agent installer.

Lets the user choose, per model, whether to install it and whether to autostart
it (load it at boot), and to add their own models by typing a name + download
URL. The menu emits a selection dict that
``lmstudio_models.apply_install_selection`` turns into a customized catalog, so
the installer downloads and boot-loads exactly what the user picked.

The selection state and command handling are kept pure (no terminal I/O) so they
are unit-tested directly; :func:`run_menu` is the thin interactive wrapper the
installer calls on a TTY, reading lines via an injected ``reader`` and writing
via an injected ``writer`` so it is testable end-to-end with scripted input.

Usage (CLI, invoked by the installer on a TTY):
  lmstudio_install_menu.py --catalog model-profiles.json --out selection.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_MODELS_PATH = Path(__file__).resolve().parent / "lmstudio_models.py"
_spec = importlib.util.spec_from_file_location("lmstudio_models", _MODELS_PATH)
assert _spec and _spec.loader
lmstudio_models = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("lmstudio_models", lmstudio_models)
_spec.loader.exec_module(lmstudio_models)


class SelectionState:
    """Mutable per-model install/autostart selection plus user-added models.

    Attributes:
        rows: Ordered list of ``{"id", "label", "install", "autostart", "custom"}``
            dicts, one per model (shipped models first, then user-added ones).
    """

    def __init__(self, profiles: list[dict]):
        """Seed the menu from registry profiles, preselecting the shipped defaults.

        Args:
            profiles: The registry's profile list. Each profile's
                ``default_install``/``default_keep_on``/``default_main`` seeds the
                initial install/autostart state so the menu opens on the shipped
                layout.
        """
        self.rows: list[dict] = []
        for profile in profiles:
            install = bool(profile.get("default_install"))
            autostart = install and bool(
                profile.get("default_keep_on") or profile.get("default_main")
            )
            self.rows.append({
                "id": profile["id"],
                "label": profile.get("description") or profile["id"],
                "install": install,
                "autostart": autostart,
                "custom": bool(profile.get("custom")),
                "profile": profile if profile.get("custom") else None,
            })

    def _row(self, index: int) -> dict | None:
        """Return the row at a 1-based index, or None when out of range."""
        if 1 <= index <= len(self.rows):
            return self.rows[index - 1]
        return None

    def toggle_install(self, index: int) -> None:
        """Toggle install for a row; deselecting also clears autostart."""
        row = self._row(index)
        if row is None:
            return
        row["install"] = not row["install"]
        if not row["install"]:
            row["autostart"] = False

    def toggle_autostart(self, index: int) -> None:
        """Toggle autostart for a row; enabling it also selects the model."""
        row = self._row(index)
        if row is None:
            return
        row["autostart"] = not row["autostart"]
        if row["autostart"]:
            row["install"] = True

    def set_all(self, install: bool) -> None:
        """Select or clear install on every row (autostart cleared when off)."""
        for row in self.rows:
            row["install"] = install
            if not install:
                row["autostart"] = False

    def add_custom(self, spec: dict) -> dict:
        """Add (or replace) a user-typed custom model, selected and autostarted.

        Args:
            spec: Partial profile (see
                :func:`lmstudio_models.normalize_custom_profile`).

        Returns:
            dict: The normalized profile that was added.
        """
        profile = lmstudio_models.normalize_custom_profile(spec)
        self.rows = [r for r in self.rows if r["id"] != profile["id"]]
        self.rows.append({
            "id": profile["id"],
            "label": profile.get("description") or profile["id"],
            "install": True,
            "autostart": True,
            "custom": True,
            "profile": profile,
        })
        return profile

    def selection(self) -> dict:
        """Return the selection payload for ``apply_install_selection``."""
        selected = [r["id"] for r in self.rows if r["install"]]
        autostart = [r["id"] for r in self.rows if r["install"] and r["autostart"]]
        custom = [r["profile"] for r in self.rows if r["custom"] and r["profile"]]
        return {
            "selected_ids": selected,
            "autostart_ids": autostart,
            "custom_profiles": custom,
        }


def render(state: SelectionState) -> str:
    """Return the text table shown to the user for the current state."""
    lines = ["", "  LM Studio models — choose what to install and autostart:", ""]
    for i, row in enumerate(state.rows, start=1):
        inst = "x" if row["install"] else " "
        auto = "boot" if row["autostart"] else "    "
        tag = " (custom)" if row["custom"] else ""
        lines.append(f"   {i:>2}. [{inst}] [{auto}] {row['label']}{tag}")
    lines += [
        "",
        "  Commands: <n> toggle install · b<n> toggle autostart · a add your own model",
        "            all / none · enter to install the selection",
        "",
    ]
    return "\n".join(lines)


def _prompt_custom(reader, writer) -> dict | None:
    """Prompt for the fields of a user-added model. Returns None if cancelled."""
    writer("\n  Add your own model (blank name cancels).\n")
    name = reader("    Name (e.g. 'Llama 3 8B'): ").strip()
    if not name:
        return None
    model_key = reader("    lms model key (as `lms load` names it): ").strip()
    url = reader("    Download URL (.gguf, blank to skip download): ").strip()
    sha = reader("    sha256 (optional, blank to skip verify): ").strip()
    return {"name": name, "model_key": model_key, "download_url": url, "sha256": sha}


def run_menu(profiles: list[dict], *, reader=input, writer=None) -> dict:
    """Drive the interactive selection menu and return the selection payload.

    Args:
        profiles: The registry profile list to seed the menu.
        reader: Callable taking a prompt and returning a line (defaults to
            :func:`input`).
        writer: Callable that prints a string (defaults to stdout).

    Returns:
        dict: ``{"selected_ids", "autostart_ids", "custom_profiles"}``.
    """
    if writer is None:
        def writer(text: str) -> None:
            sys.stdout.write(text)

    state = SelectionState(profiles)
    while True:
        writer(render(state))
        try:
            command = reader("  > ").strip()
        except EOFError:
            break
        low = command.lower()
        if low in ("", "done", "q", "ok"):
            break
        if low in ("a", "add"):
            spec = _prompt_custom(reader, writer)
            if spec:
                try:
                    state.add_custom(spec)
                except ValueError as exc:
                    writer(f"\n  Could not add model: {exc}\n")
            continue
        if low == "all":
            state.set_all(True)
            continue
        if low == "none":
            state.set_all(False)
            continue
        if low[:1] == "b" and low[1:].strip().isdigit():
            state.toggle_autostart(int(low[1:].strip()))
            continue
        if low.isdigit():
            state.toggle_install(int(low))
            continue
        writer(f"\n  Unrecognized command: {command!r}\n")
    return state.selection()


def main(argv: list[str] | None = None) -> int:
    """CLI entry: run the menu against a catalog and write the selection JSON."""
    parser = argparse.ArgumentParser(description="Interactive LM Studio model selection.")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="Write selection JSON here.")
    args = parser.parse_args(argv)

    registry = lmstudio_models.load_registry(args.catalog)
    selection = run_menu(lmstudio_models.profiles(registry))
    args.out.write_text(json.dumps(selection, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
