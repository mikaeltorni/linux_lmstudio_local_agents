# Linux LM Studio Local Agents — AGENTS.md

This repository owns LM Studio local-agent serving: installing the LM Studio
AppImage/CLI, running the headless OpenAI-compatible server as a systemd user
service, and managing the local GGUF model registry + switch/keep-on/main logic.
It was extracted from `agent_command_center` and is the single source of truth
for local model serving.

## Where things live

- `configs/lmstudio/model-profiles.json` — the model registry (the single source
  of truth for model keys, download URLs, sha256, serve flags, and the
  `switch_env_var`/`default_keep_on`/`default_main` install fields).
- `src/lib.rs` and `src/bin/lmstudio-model.rs` — registry resolution and the
  `lmstudio-model` CLI that switches the GPU-resident model via `lms`.
- `src/menu.rs` and `src/bin/lmstudio-install-menu.rs` — the interactive
  install-time model menu and selection state. Keep selection logic pure and
  testable, with TTY I/O in the wrapper. The core (`lm_studio_app` + `serve`)
  installs and serves regardless of model choice; `models` is the optional,
  fully customizable layer.
- `scripts/lmstudio_models.py`, `scripts/lmstudio_model_selector.py`, and
  `scripts/lmstudio_install_menu.py` — retained Python implementations with
  tests; the registry implementation also serves as a Rust parity reference.
- `lib/lm_studio.sh` — LM Studio install + serve.env + boot script + user
  service management.

## Deployment safety

GNOME/desktop deployment safety is owned by the `linux-configuration` skill.
Apply changes silently from the command line: restart the user service with
`systemctl --user restart lmstudio-serve.service`; never trigger a GNOME Shell
reload, logout, or reboot. Switching models is a runtime `lms` operation and
needs no session restart.

## Standalone first

A missing sibling repository must never block installation. The component
framework is resolved from a sibling checkout or downloaded on demand. Consumers
(`agent_command_center`, `linux_programming_setup`) depend on this repo, not the
other way around.
