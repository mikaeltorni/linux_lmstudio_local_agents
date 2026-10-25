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
import os
import sys
from pathlib import Path

_MODELS_PATH = Path(__file__).resolve().parent / "lmstudio_models.py"
_spec = importlib.util.spec_from_file_location("lmstudio_models", _MODELS_PATH)
assert _spec and _spec.loader
lmstudio_models = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("lmstudio_models", lmstudio_models)
_spec.loader.exec_module(lmstudio_models)

_shared_path = Path(os.environ.get("ISC_REPEATABLE_FIELDS_PY", ""))
if not _shared_path.is_file():
    _shared_path = Path(__file__).resolve().parents[1] / ".installer-support" / "repeatable_fields.py"
_rows_spec = importlib.util.spec_from_file_location("isc_repeatable_fields", _shared_path)
if not _rows_spec or not _rows_spec.loader:
    raise RuntimeError("shared repeatable_fields.py is unavailable")
repeatable_fields = importlib.util.module_from_spec(_rows_spec)
_rows_spec.loader.exec_module(repeatable_fields)


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
                ``default_install`` seeds the install state and
                :func:`lmstudio_models.autostarts_by_default` seeds autostart, so
                the menu opens on the shipped layout (a model with
                ``default_startup: false`` opens installed but startup-off).
        """
        self._state = repeatable_fields.RepeatableRows()
        self.rows = self._state.items
        for profile in profiles:
            install = bool(profile.get("default_install"))
            autostart = install and lmstudio_models.autostarts_by_default(profile)
            self._state.add({
                "id": profile["id"],
                "label": profile.get("description") or profile["id"],
                "install": install,
                "autostart": autostart,
                "custom": bool(profile.get("custom")),
                "switch_env_var": profile.get("switch_env_var", ""),
                "profile": profile if profile.get("custom") else None,
                "group": int(profile.get("group") or 0),
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

    def set_environment(self, index: int, value: str) -> None:
        """Customize a row's validated switch environment variable."""
        row = self._row(index)
        if row is None:
            return
        candidate = lmstudio_models.normalize_custom_profile({
            **(row.get("profile") or {"name": row["id"], "model_key": row["id"]}),
            "switch_env_var": value,
        })
        row["switch_env_var"] = candidate["switch_env_var"]
        if row.get("profile"):
            row["profile"]["switch_env_var"] = candidate["switch_env_var"]

    def set_group(self, index: int, group: int) -> None:
        """Assign zero (ungrouped) or an exclusivity group number."""
        if self._state.set_group(index, group):
            row = self._row(index)
            if row and row.get("profile"):
                row["profile"]["group"] = group

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
        profile = dict(spec) if spec.get("custom") else lmstudio_models.normalize_custom_profile(spec)
        self.rows[:] = [r for r in self.rows if r["id"] != profile["id"]]
        self._state.add({
            "id": profile["id"],
            "label": profile.get("description") or profile["id"],
            "install": True,
            "autostart": True,
            "custom": True,
            "switch_env_var": profile.get("switch_env_var", ""),
            "profile": profile,
            "group": int(profile.get("group") or 0),
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
        switch = (
            f"  [env: {row['switch_env_var']}]" if row.get("switch_env_var") else ""
        )
        group = f"  [group: {row['group']}]" if row.get("group") else ""
        lines.append(f"   {i:>2}. [{inst}] [{auto}] {row['label']}{tag}{switch}{group}")
    lines += [
        "",
        "  Commands: <n> toggle install · s<n> toggle startup · A add another URL/spec",
        "            e<n> edit env variable · g<n> set exclusivity group (0 = none)",
        "            all / none · enter to install the selection",
        "",
    ]
    return "\n".join(lines)


def _prompt_custom(reader, writer) -> dict | None:
    """Prompt for exactly one model source line. Returns None if cancelled."""
    writer("\n  Add a model (blank cancels).\n")
    source = reader("    Hugging Face URL or owner/model@quant: ").strip()
    if not source:
        return None
    return lmstudio_models.custom_profile_from_source(source)


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
        if low[:1] in {"b", "s"} and low[1:].strip().isdigit():
            state.toggle_autostart(int(low[1:].strip()))
            continue
        if low[:1] == "e" and low[1:].strip().isdigit():
            try:
                value = reader("    Environment variable (blank disables switching): ").strip()
                state.set_environment(int(low[1:].strip()), value)
            except ValueError as exc:
                writer(f"\n  Could not set environment variable: {exc}\n")
            continue
        if low[:1] == "g" and low[1:].strip().isdigit():
            value = reader("    Group number (0 = none): ").strip()
            if value.isdigit():
                state.set_group(int(low[1:].strip()), int(value))
            else:
                writer("\n  Group must be a non-negative number.\n")
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
