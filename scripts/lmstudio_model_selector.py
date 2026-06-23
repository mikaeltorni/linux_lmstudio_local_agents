#!/usr/bin/env python3
"""Switch the active GPU-resident LM Studio model and persist the choice.

Installed as ``lmstudio-model``. Used by the boot service (``--restore``), by the
per-model switch wrappers, and directly by users to move the single switchable
"main" slot between models. Keep-on models (e.g. the CPU-resident Qwen coder) are
loaded separately by the boot script and are not unloaded here.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from lmla_logging_utils import get_logger, log_call

LOGGER = get_logger("lmstudio_model_selector", "lmstudio_model_selector.log")


def load_profiles(path: Path) -> list[dict]:
    """Load model profiles from the catalog."""
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)["profiles"]


def selected_profile(profiles: list[dict], requested: str | None, state_file: Path) -> dict:
    """Resolve the persisted or default model profile."""
    selected = requested or ""
    if requested is None and state_file.is_file():
        selected = state_file.read_text(encoding="utf-8").strip()
    if requested is None and not selected:
        selected = next(profile["id"] for profile in profiles if profile.get("boot_preload"))
    for profile in profiles:
        if selected in (profile["id"], profile.get("command"), profile["model_key"]):
            return profile
    raise ValueError(f"Unknown LM Studio model profile: {selected}")


def run_lms(lms: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run an LM Studio CLI command."""
    return subprocess.run(
        [str(lms), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def profile_identifier(profile: dict) -> str:
    """Return the stable API identifier for a profile."""
    return profile.get("identifier") or profile["model_key"]


def model_matches_profile(item: dict, profile: dict) -> bool:
    """Return whether an LM Studio catalog item matches a profile."""
    model_path = profile.get("model_path") or ""
    model_key = profile["model_key"]
    identifiers = {
        item.get("modelKey") or "",
        item.get("path") or "",
        item.get("indexedModelIdentifier") or "",
    }
    if model_key in identifiers or model_path in identifiers:
        return True
    if model_path:
        model_name = Path(model_path).name
        return any(value.endswith(model_name) for value in identifiers if value)
    return False


def resolve_load_key(profile: dict, lms: Path) -> str:
    """Resolve a profile to the local LM Studio model key used by `lms load`."""
    listed = run_lms(lms, "ls", "--json", check=False)
    if listed.returncode == 0:
        try:
            for item in json.loads(listed.stdout):
                if model_matches_profile(item, profile):
                    return item.get("modelKey") or profile["model_key"]
        except json.JSONDecodeError:
            LOGGER.warning("Could not parse `lms ls --json`; using configured model key.")
    elif listed.stderr.strip():
        LOGGER.warning("`lms ls --json` failed: %s", listed.stderr.strip())
    return profile["model_key"]


@log_call(LOGGER)
def load_model(profile: dict, lms: Path) -> subprocess.CompletedProcess[str]:
    """Load one profile and return the CLI result."""
    load_key = resolve_load_key(profile, lms)
    LOGGER.info("Loading LM Studio model: %s", profile_identifier(profile))
    args = [
        "load",
        "-c",
        str(profile["context_length"]),
        "--gpu",
        str(profile["gpu"]),
        "--parallel",
        str(profile["parallel"]),
        "--identifier",
        profile_identifier(profile),
    ]
    if profile.get("ttl"):
        args.extend(["--ttl", str(profile["ttl"])])
    args.extend([load_key, "-y"])
    return run_lms(lms, *args, check=False)


def switchable_profiles(profiles: list[dict]) -> list[dict]:
    """Return profiles eligible for the switchable main slot (not keep-on)."""
    return [profile for profile in profiles if not profile.get("default_keep_on")]


def switch_model(profile: dict, profiles: list[dict], lms: Path) -> None:
    """Unload the other switchable models and load the selected profile.

    Keep-on profiles (``default_keep_on``) are left loaded so editor autocomplete
    and other always-on models stay available while the main slot changes.
    """
    LOGGER.info("Starting LM Studio server if needed.")
    server_result = run_lms(lms, "server", "start", check=False)
    if server_result.returncode and server_result.stderr.strip():
        LOGGER.warning("`lms server start` returned non-zero: %s", server_result.stderr.strip())
    loaded = run_lms(lms, "ps", "--json", check=False)
    previous = None
    candidates = switchable_profiles(profiles)
    if loaded.returncode == 0:
        try:
            identifiers = {
                item.get("identifier") or item.get("modelKey")
                for item in json.loads(loaded.stdout)
            }
            previous = next(
                (
                    candidate for candidate in candidates
                    if profile_identifier(candidate) in identifiers
                    or candidate["model_key"] in identifiers
                ),
                None,
            )
        except json.JSONDecodeError:
            LOGGER.warning("Could not parse `lms ps --json`; previous model restore will be unavailable.")
    elif loaded.stderr.strip():
        LOGGER.warning("`lms ps --json` failed: %s", loaded.stderr.strip())

    for candidate in candidates:
        if candidate["id"] == profile["id"]:
            continue
        unload_keys = {candidate["model_key"], profile_identifier(candidate)}
        unload_keys.add(resolve_load_key(candidate, lms))
        for unload_key in unload_keys:
            result = run_lms(lms, "unload", unload_key, check=False)
            if result.returncode and result.stderr.strip():
                LOGGER.warning("Failed to unload %s: %s", unload_key, result.stderr.strip())

    result = load_model(profile, lms)
    if result.returncode:
        sys.stderr.write(result.stderr or result.stdout)
        if previous and previous["id"] != profile["id"]:
            LOGGER.warning("Restoring previous model: %s", profile_identifier(previous))
            restore_result = load_model(previous, lms)
            if restore_result.returncode:
                LOGGER.error("Failed to restore previous model: %s", profile_identifier(previous))
        raise RuntimeError(f"Failed to load LM Studio model: {profile_identifier(profile)}")
    LOGGER.info("Active LM Studio model: %s", profile_identifier(profile))


def default_catalog() -> Path:
    """Return the installed model catalog path."""
    return Path.home() / ".config/lmstudio-local-agents/configs/lmstudio/model-profiles.json"


def main() -> int:
    """Run the command-line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", nargs="?", help="profile id or command")
    parser.add_argument("--restore", action="store_true", help="restore the persisted profile")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--state-file", type=Path)
    args = parser.parse_args()

    home = Path.home()
    catalog = args.catalog or default_catalog()
    state_file = args.state_file or home / ".config/lm-studio/selected-model"
    lms = Path(os.environ.get("LMS_CLI", home / ".lmstudio/bin/lms"))
    profiles = load_profiles(catalog)
    if args.restore and args.profile:
        parser.error("--restore cannot be combined with a profile")
    if not args.restore and not args.profile:
        parser.error("a profile or --restore is required")
    profile = selected_profile(profiles, None if args.restore else args.profile, state_file)

    switch_model(profile, profiles, lms)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(profile["id"] + "\n", encoding="utf-8")
    print(profile["model_key"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        LOGGER.error("lmstudio-model: %s", exc)
        raise SystemExit(1)
