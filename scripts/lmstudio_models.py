#!/usr/bin/env python3
"""Model registry and selection/resolution logic for LM Studio local agents.

Pure, side-effect-free helpers (all filesystem and ``lms`` I/O lives in the
installer and the model selector) that turn the registry in
``configs/lmstudio/model-profiles.json`` plus a set of install-time choices into a
concrete plan:

  * which models to install (download),
  * which stay loaded at all times ("keep-on"),
  * which model is the switchable "main" slot,
  * and which environment variable switches the active model to each profile.

At runtime the same logic resolves the active main model from the environment, so
the per-model switch variables and the top-level ``main_env_var`` behave
identically in tests and on the machine.

Usage (CLI, consumed by the boot script):
  lmstudio_models.py active-main --catalog model-profiles.json   # prints model_key
  lmstudio_models.py keep-on     --catalog model-profiles.json   # prints model_keys
  lmstudio_models.py default-plan --catalog model-profiles.json  # prints JSON plan
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

#: Environment values treated as "on" for switch variables.
TRUTHY = {"1", "true", "yes", "y", "on"}

#: Fallback top-level switch variable when the registry omits ``main_env_var``.
DEFAULT_MAIN_ENV_VAR = "LMSTUDIO_MAIN_MODEL"


def is_truthy(value: object) -> bool:
    """Return whether an environment value should be treated as enabled.

    Args:
        value: Raw environment value (or ``None``).

    Returns:
        bool: True when the normalized value is one of :data:`TRUTHY`.
    """
    return str(value if value is not None else "").strip().lower() in TRUTHY


def load_registry(path: str | Path) -> dict:
    """Load and minimally validate the model registry JSON.

    Args:
        path: Path to ``model-profiles.json``.

    Returns:
        dict: The parsed registry.

    Raises:
        ValueError: When the file has no ``profiles`` list.
    """
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data.get("profiles"), list):
        raise ValueError("model registry must contain a 'profiles' list")
    return data


def profiles(registry: dict) -> list[dict]:
    """Return the registry's profile list."""
    return list(registry.get("profiles", []))


def main_env_var(registry: dict) -> str:
    """Return the top-level switch variable name (with a safe default)."""
    return registry.get("main_env_var") or DEFAULT_MAIN_ENV_VAR


def find_profile(registry: dict, identifier: str) -> dict | None:
    """Resolve a profile by id, command, or model_key.

    Args:
        registry: The loaded registry.
        identifier: An id, command alias, or model key.

    Returns:
        dict | None: The matching profile, or ``None``.
    """
    for profile in profiles(registry):
        if identifier and identifier in (profile.get("id"), profile.get("command"), profile.get("model_key")):
            return profile
    return None


@dataclass(frozen=True)
class ModelPlan:
    """A resolved set of install-time model choices.

    Attributes:
        install_ids: Profile ids selected for installation, in registry order.
        keep_on_ids: Subset of ``install_ids`` kept loaded at all times.
        main_id: The default active "main" model id (must be installed).
        switch_env_vars: Mapping of installed model id to its switch variable.
        main_env_var: The top-level variable that names the active main model.
    """

    install_ids: tuple[str, ...]
    keep_on_ids: tuple[str, ...]
    main_id: str
    switch_env_vars: dict[str, str]
    main_env_var: str

    def keep_on_model_keys(self, registry: dict) -> list[str]:
        """Return the model keys for the keep-on models, in registry order."""
        return [find_profile(registry, pid)["model_key"] for pid in self.keep_on_ids]

    def as_dict(self) -> dict:
        """Return a JSON-serializable view of the plan."""
        return {
            "install_ids": list(self.install_ids),
            "keep_on_ids": list(self.keep_on_ids),
            "main_id": self.main_id,
            "switch_env_vars": dict(self.switch_env_vars),
            "main_env_var": self.main_env_var,
        }


def _ordered_ids(registry: dict, ids: set[str]) -> tuple[str, ...]:
    """Return the requested ids in registry order (stable, de-duplicated)."""
    return tuple(p["id"] for p in profiles(registry) if p["id"] in ids)


def build_plan(
    registry: dict,
    *,
    install_ids: list[str],
    keep_on_ids: list[str],
    main_id: str,
    switch_env_vars: dict[str, str] | None = None,
) -> ModelPlan:
    """Build and validate a model plan from explicit choices.

    Args:
        registry: The loaded registry.
        install_ids: Profile ids to install.
        keep_on_ids: Profile ids to keep loaded (must be a subset of installs).
        main_id: The default active main model id (must be installed).
        switch_env_vars: Optional id->variable overrides; missing entries fall
            back to each profile's ``switch_env_var``.

    Returns:
        ModelPlan: The validated plan.

    Raises:
        ValueError: On unknown ids, keep-on/main not installed, or empty install.
    """
    install_set = set(install_ids)
    if not install_set:
        raise ValueError("at least one model must be selected for installation")
    for pid in install_set:
        if find_profile(registry, pid) is None or find_profile(registry, pid)["id"] != pid:
            raise ValueError(f"unknown model id: {pid}")
    keep_on_set = set(keep_on_ids)
    extra_keep = keep_on_set - install_set
    if extra_keep:
        raise ValueError(f"keep-on models not in install set: {sorted(extra_keep)}")
    if main_id not in install_set:
        raise ValueError(f"main model '{main_id}' is not in the install set")

    resolved_switch: dict[str, str] = {}
    for pid in _ordered_ids(registry, install_set):
        override = (switch_env_vars or {}).get(pid)
        resolved_switch[pid] = override or find_profile(registry, pid).get("switch_env_var") or ""

    return ModelPlan(
        install_ids=_ordered_ids(registry, install_set),
        keep_on_ids=_ordered_ids(registry, keep_on_set),
        main_id=main_id,
        switch_env_vars=resolved_switch,
        main_env_var=main_env_var(registry),
    )


def default_plan(registry: dict) -> ModelPlan:
    """Build the default (non-interactive) plan from the registry's flags.

    Installs every ``default_install`` profile, keeps on every
    ``default_keep_on`` profile, and uses the single ``default_main`` profile as
    the active main model.

    Args:
        registry: The loaded registry.

    Returns:
        ModelPlan: The default plan.

    Raises:
        ValueError: When zero or multiple profiles are marked ``default_main``.
    """
    install_ids = [p["id"] for p in profiles(registry) if p.get("default_install")]
    keep_on_ids = [p["id"] for p in profiles(registry) if p.get("default_install") and p.get("default_keep_on")]
    mains = [p["id"] for p in profiles(registry) if p.get("default_install") and p.get("default_main")]
    # A catalog with nothing installed (the "core only" selection) has an empty
    # plan: no models to download, keep on, or boot-load.
    if not install_ids:
        return ModelPlan((), (), "", {}, main_env_var(registry))
    if len(mains) != 1:
        raise ValueError(f"exactly one installed profile must set default_main, found {mains}")
    return build_plan(
        registry,
        install_ids=install_ids,
        keep_on_ids=keep_on_ids,
        main_id=mains[0],
    )


def resolve_active_main(registry: dict, plan: ModelPlan, env: dict[str, str] | None = None) -> dict:
    """Resolve the active main model from the environment.

    Precedence:
      1. the first installed model (registry order) whose ``switch_env_var`` is
         truthy in ``env``;
      2. otherwise the model named by ``main_env_var`` when it is installed;
      3. otherwise ``plan.main_id``.

    Args:
        registry: The loaded registry.
        plan: A resolved plan.
        env: Environment mapping; defaults to ``os.environ``.

    Returns:
        dict: The active main profile.
    """
    environ = os.environ if env is None else env
    for pid in plan.install_ids:
        var = plan.switch_env_vars.get(pid)
        if var and is_truthy(environ.get(var)):
            return find_profile(registry, pid)
    named = (environ.get(plan.main_env_var) or "").strip()
    if named:
        candidate = find_profile(registry, named)
        if candidate and candidate["id"] in plan.install_ids:
            return candidate
    return find_profile(registry, plan.main_id)


#: Fields a boot loader needs to run ``lms load`` for one model, in order.
BOOT_LOAD_FIELDS = ("model_key", "context_length", "gpu", "parallel", "ttl", "identifier")


def boot_load_specs(registry: dict, plan: ModelPlan, env: dict[str, str] | None = None) -> list[dict]:
    """Return the models the boot service should load, in load order.

    Keep-on models come first (always loaded), then the resolved active main
    model unless it is already a keep-on model. Each spec carries the ``lms
    load`` flags from the profile.

    Args:
        registry: The loaded registry.
        plan: A resolved plan.
        env: Environment mapping; defaults to ``os.environ``.

    Returns:
        list[dict]: One dict per model with the :data:`BOOT_LOAD_FIELDS` keys.
    """
    if not plan.install_ids:
        return []
    order: list[dict] = []
    seen: set[str] = set()

    def add(profile: dict) -> None:
        if profile["id"] in seen:
            return
        seen.add(profile["id"])
        order.append({
            "model_key": profile["model_key"],
            "context_length": profile["context_length"],
            "gpu": profile["gpu"],
            "parallel": profile["parallel"],
            "ttl": profile.get("ttl", 0),
            "identifier": profile.get("identifier") or profile["model_key"],
        })

    for pid in plan.keep_on_ids:
        add(find_profile(registry, pid))
    add(resolve_active_main(registry, plan, env))
    return order


def _parse_selection(raw: str | None) -> list[str]:
    """Split a comma/space separated model-id selection string."""
    return [token for token in (raw or "").replace(",", " ").split() if token]


def installable_ids(registry: dict, selected: list[str] | None = None) -> list[str]:
    """Return the model ids to install, in registry order.

    Args:
        registry: The loaded registry.
        selected: Explicit subset of ids; when falsy, every ``default_install``
            profile is used.

    Returns:
        list[str]: Ordered model ids.

    Raises:
        ValueError: When the selection names an unknown id.
    """
    known = {profile["id"] for profile in profiles(registry)}
    if selected:
        unknown = set(selected) - known
        if unknown:
            raise ValueError(f"unknown model ids: {sorted(unknown)}")
        wanted = set(selected)
        return [profile["id"] for profile in profiles(registry) if profile["id"] in wanted]
    return [profile["id"] for profile in profiles(registry) if profile.get("default_install")]


#: Fields the downloader needs per model, in order.
DOWNLOAD_FIELDS = ("id", "download_url", "model_path", "sha256")


def download_specs(registry: dict, selected: list[str] | None = None) -> list[dict]:
    """Return download specs for installable models that have a download URL."""
    specs = []
    for pid in installable_ids(registry, selected):
        profile = find_profile(registry, pid)
        if profile.get("download_url"):
            specs.append({field: profile.get(field, "") for field in DOWNLOAD_FIELDS})
    return specs


def slugify_id(value: str) -> str:
    """Return a filesystem/command-safe lowercase slug for a custom model id.

    Args:
        value: Free-text model name typed by the user.

    Returns:
        str: Lowercased value with runs of non-alphanumeric characters collapsed
        to single dashes and leading/trailing dashes stripped.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug


def switch_env_var_for(model_id: str) -> str:
    """Return the per-model switch variable name for a model id.

    Args:
        model_id: A profile id (typically a slug from :func:`slugify_id`).

    Returns:
        str: ``LMSTUDIO_USE_<UPPER>`` with non-alphanumerics turned into ``_``.
    """
    suffix = re.sub(r"[^A-Z0-9]+", "_", str(model_id).upper()).strip("_")
    return f"LMSTUDIO_USE_{suffix}"


#: Per-model serving defaults applied to a user-added custom model.
CUSTOM_PROFILE_DEFAULTS = {
    "gpu": "max",
    "context_length": 131072,
    "parallel": 1,
    "ttl": 0,
    "identifier": "",
    "sha256": "",
    "yes": True,
}


def normalize_custom_profile(spec: dict) -> dict:
    """Turn user-entered fields into a complete model profile.

    A custom model only requires a name/id, a ``model_key`` (the id ``lms load``
    uses), and a ``download_url``. Everything else is derived or defaulted so the
    profile behaves exactly like a shipped one (switch variable, command alias,
    download path, serving flags).

    Args:
        spec: Partial profile with at least ``id`` (or ``name``) and
            ``model_key``. ``download_url`` is required to actually download.

    Returns:
        dict: A fully populated profile, marked ``default_install`` (a user who
        types a model in wants it installed). Keep-on/main flags are decided by
        :func:`apply_install_selection`.

    Raises:
        ValueError: When no id/name or model_key can be determined.
    """
    raw_id = spec.get("id") or spec.get("name") or ""
    model_id = slugify_id(raw_id)
    if not model_id:
        raise ValueError("a custom model needs a name")
    model_key = (spec.get("model_key") or "").strip() or model_id
    download_url = (spec.get("download_url") or "").strip()
    if download_url:
        parsed_url = urlparse(download_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("custom model download URL must be an HTTP(S) URL")
    sha256 = str(spec.get("sha256") or "").strip()
    if sha256 and not re.fullmatch(r"[A-Fa-f0-9]{64}", sha256):
        raise ValueError("custom model sha256 must contain exactly 64 hexadecimal characters")
    model_path = (spec.get("model_path") or "").strip()
    if not model_path:
        if download_url:
            base = download_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            model_path = f"{model_id}/{base}" if base else f"{model_id}.gguf"
        else:
            model_path = f"{model_id}.gguf"

    if "switch_env_var" in spec:
        switch_env_var = str(spec.get("switch_env_var") or "").strip()
    else:
        switch_env_var = switch_env_var_for(model_id)
    if switch_env_var and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", switch_env_var):
        raise ValueError(f"invalid switch environment variable: {switch_env_var}")

    profile = dict(CUSTOM_PROFILE_DEFAULTS)
    profile.update({
        "id": model_id,
        "command": spec.get("command") or slugify_id(raw_id).replace("-", ""),
        "description": (spec.get("description") or spec.get("name") or raw_id or model_id),
        "model_key": model_key,
        "download_url": download_url,
        "model_path": model_path,
        "switch_env_var": switch_env_var,
        "default_install": True,
        "default_keep_on": False,
        "default_main": False,
        "custom": True,
    })
    # Allow explicit serving-flag overrides from the spec.
    for key in ("gpu", "context_length", "parallel", "ttl", "identifier"):
        if spec.get(key) not in (None, ""):
            profile[key] = spec[key]
    if sha256:
        profile["sha256"] = sha256.lower()
    return profile


def custom_install_selection(registry: dict, spec: dict, *, role: str) -> dict:
    """Build an additive selection payload for one consumer-supplied model.

    Args:
        registry: The user's current catalog, whose installed models are kept.
        spec: Custom model fields accepted by normalize_custom_profile.
        role: main for a switchable coding model or keep-on for an always-loaded
            autocomplete model.

    Returns:
        dict: Selection payload accepted by apply_install_selection.

    Raises:
        ValueError: When role is unsupported or the custom profile is invalid.
    """
    if role not in {"main", "keep-on"}:
        raise ValueError(f"unsupported custom model role: {role}")
    custom = normalize_custom_profile(spec)
    selected = [
        profile["id"] for profile in profiles(registry)
        if profile.get("default_install") and profile["id"] != custom["id"]
    ]
    selected.append(custom["id"])
    autostart = [
        profile["id"] for profile in profiles(registry)
        if profile.get("default_install")
        and (profile.get("default_keep_on") or profile.get("default_main"))
        and profile["id"] != custom["id"]
    ]
    autostart.append(custom["id"])
    current_main = next(
        (
            profile["id"] for profile in profiles(registry)
            if profile.get("default_install") and profile.get("default_main")
        ),
        None,
    )
    return {
        "selected_ids": selected,
        "autostart_ids": autostart,
        "main_id": custom["id"] if role == "main" else current_main,
        "custom_profiles": [custom],
    }


def apply_install_selection(
    registry: dict,
    *,
    selected_ids: list[str],
    autostart_ids: list[str],
    main_id: str | None = None,
    custom_profiles: list[dict] | None = None,
) -> dict:
    """Return a copy of the registry rewritten to encode an install selection.

    The returned registry is a drop-in for :func:`default_plan`: its
    ``default_install`` / ``default_keep_on`` / ``default_main`` flags reflect
    exactly the user's choices, so every downstream consumer (download list,
    boot-load, switch commands) follows the selection with no other changes.

    Autostart maps to "loaded at boot": autostarted models become keep-on models
    and the resolved main model is always loaded too, so a model that is selected
    but not autostarted is installed and runtime-switchable yet not loaded at
    boot.

    Args:
        registry: The base registry (shipped catalog).
        selected_ids: Profile ids to install. Must be non-empty.
        autostart_ids: Subset of ``selected_ids`` to load at boot.
        main_id: Optional explicit default active model; must be selected. When
            omitted it is derived from the registry's ``default_main`` flag among
            selected models, else the first autostarted model, else the first
            selected model.
        custom_profiles: User-added profiles (already normalized or raw specs)
            appended to the registry before applying the selection.

    Returns:
        dict: A new registry dict with updated profiles and flags.

    Raises:
        ValueError: When the selection is empty, names an unknown id, or
            autostarts a model that is not selected.
    """
    import copy

    new_registry = copy.deepcopy(registry)
    base_profiles = new_registry.setdefault("profiles", [])
    known = {p["id"] for p in base_profiles}
    for spec in custom_profiles or []:
        profile = spec if spec.get("custom") else normalize_custom_profile(spec)
        if profile["id"] in known:
            # Replace an existing profile of the same id (re-adding/editing).
            base_profiles[:] = [p for p in base_profiles if p["id"] != profile["id"]]
        base_profiles.append(profile)
        known.add(profile["id"])

    selected = [pid for pid in selected_ids]
    unknown = [pid for pid in selected if pid not in known]
    if unknown:
        raise ValueError(f"unknown model ids: {sorted(set(unknown))}")
    selected_set = set(selected)
    autostart_set = set(autostart_ids)
    extra = autostart_set - selected_set
    if extra:
        raise ValueError(f"autostart models not selected for install: {sorted(extra)}")

    order = [p["id"] for p in base_profiles]
    autostart_ordered = [pid for pid in order if pid in autostart_set]

    # An empty selection is the "core only" choice: no installs, no main, no
    # boot models. apply_install_selection stays total so a user who picks zero
    # models never crashes the installer.
    resolved_main = None
    if selected_set:
        if main_id and main_id in selected_set:
            resolved_main = main_id
        if resolved_main is None:
            for p in base_profiles:
                if p["id"] in selected_set and p.get("default_main"):
                    resolved_main = p["id"]
                    break
        if resolved_main is None:
            resolved_main = autostart_ordered[0] if autostart_ordered else next(pid for pid in order if pid in selected_set)

    keep_on = {pid for pid in autostart_set if pid != resolved_main}
    for profile in base_profiles:
        pid = profile["id"]
        profile["default_install"] = pid in selected_set
        profile["default_keep_on"] = pid in keep_on
        profile["default_main"] = pid == resolved_main
    return new_registry


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by the boot script, the downloader, and inspection.

    The ``download-list`` command honors the ``LMSTUDIO_INSTALL_MODELS``
    environment variable (a comma/space separated list of model ids) so a
    consumer like linux_programming_setup can request only the vibethinker model.
    """
    parser = argparse.ArgumentParser(description="LM Studio local-agent model resolution.")
    parser.add_argument(
        "command",
        choices=["active-main", "keep-on", "boot-load", "download-list", "default-plan", "apply-selection", "custom-selection"],
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument(
        "--selection",
        type=Path,
        help="apply-selection: JSON file with selected_ids/autostart_ids/main_id/custom_profiles.",
    )
    parser.add_argument("--name", default="", help="custom-selection: display name/id.")
    parser.add_argument("--model-key", default="", help="custom-selection: lms model key.")
    parser.add_argument("--download-url", default="", help="custom-selection: GGUF URL.")
    parser.add_argument("--sha256", default="", help="custom-selection: optional checksum.")
    parser.add_argument(
        "--switch-env-var",
        default=None,
        help="custom-selection: switch variable; pass an empty value for none.",
    )
    parser.add_argument(
        "--role", choices=["main", "keep-on"], default="main",
        help="custom-selection: runtime role.",
    )
    args = parser.parse_args(argv)

    registry = load_registry(args.catalog)
    if args.command == "custom-selection":
        spec = {
            "name": args.name,
            "model_key": args.model_key,
            "download_url": args.download_url,
            "sha256": args.sha256,
        }
        if args.switch_env_var is not None:
            spec["switch_env_var"] = args.switch_env_var
        print(json.dumps(custom_install_selection(registry, spec, role=args.role), indent=2))
        return 0
    if args.command == "apply-selection":
        raw = (args.selection.read_text(encoding="utf-8") if args.selection
               else sys.stdin.read())
        choice = json.loads(raw or "{}")
        customized = apply_install_selection(
            registry,
            selected_ids=choice.get("selected_ids", []),
            autostart_ids=choice.get("autostart_ids", []),
            main_id=choice.get("main_id"),
            custom_profiles=choice.get("custom_profiles", []),
        )
        print(json.dumps(customized, indent=2))
        return 0

    plan = default_plan(registry)
    if args.command == "active-main":
        if plan.install_ids:
            print(resolve_active_main(registry, plan)["model_key"])
    elif args.command == "keep-on":
        for key in plan.keep_on_model_keys(registry):
            print(key)
    elif args.command == "boot-load":
        for spec in boot_load_specs(registry, plan):
            print("\t".join(str(spec[field]) for field in BOOT_LOAD_FIELDS))
    elif args.command == "download-list":
        selected = _parse_selection(os.environ.get("LMSTUDIO_INSTALL_MODELS"))
        for spec in download_specs(registry, selected):
            print("\t".join(str(spec[field]) for field in DOWNLOAD_FIELDS))
    else:
        print(json.dumps(plan.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
