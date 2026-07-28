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
- `scripts/lmstudio_models.py` — pure, unit-tested resolution logic (default
  plan, explicit plan, runtime active-model resolution, env rendering). Keep it
  side-effect free so it stays testable; do all I/O in the installer.
- `scripts/lmstudio_model_selector.py` — `lmstudio-model` CLI that switches the
  GPU-resident model via `lms`.
- `scripts/lmstudio_install_menu.py` — interactive install-time menu for choosing
  which models to install/autostart and adding user-supplied models. Emits a
  selection that `lmstudio_models.apply_install_selection` renders into a
  customized catalog; keep the selection state pure/testable, do TTY I/O in the
  wrapper. The core (`lm_studio_app` + `serve`) installs and serves regardless of
  any model choice; `models` is the optional, fully customizable layer.
- `lib/lm_studio.sh` — LM Studio install + serve.env + boot script + user
  service management.

## Deployment safety

GNOME/desktop deployment safety is owned by the `linux-configuration` skill.
Apply changes silently from the command line: restart the user service with
`systemctl --user restart lmstudio-serve.service`; never trigger a GNOME Shell
reload, logout, or reboot. Switching models is a runtime `lms` operation and
needs no session restart.

## Clean Installation Compatibility

All changes must remain compatible with a clean install run through
`installation_scripts/install.sh` and this repository's `install.sh`. Add every
required dependency, asset, and configuration to the installer; keep steps
idempotent. Do not rely on files that exist only on the current machine.

## Standalone first

A missing sibling repository must never block installation. The component
framework is resolved from a sibling checkout or downloaded on demand. Consumers
(`agent_command_center`, `linux_programming_setup`) depend on this repo, not the
other way around.


## Mandatory programming guidelines prompt

Every agent task in this repository must load the shared
`general-programming-guidelines` skill before the first file edit, using the
harness-native invocation for the runtime in use:

- Codex-family (`ca`, `qa`, `oa`, `na`, …): `$general-programming-guidelines`
- Claude Code, Cline, Grok: `/general-programming-guidelines`
- OpenCode: load `general-programming-guidelines` with the skill tool

Agent Command Center prepends this bare invocation to every dispatched prompt, so the
harness activates the skill before reading the task. When you start a task by hand, invoke it
yourself first. Then follow its Work Loop — dedicated worktree branch before
the first edit, tests, logging, documentation — and do not report the task done
until its Definition of Done checklist passes.
Always finish the delivery: commit in the worktree, merge into the default
branch with `git merge --no-ff`, then reload whatever consumes the change
(installer, skill deployment, service, session). You do not need to be asked.
Never push to a remote and never rewrite history unless the user explicitly
requests it.

