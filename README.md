# Linux LM Studio Local Agents

> Part of [installation_scripts](https://github.com/mikaeltorni/installation_scripts) — the master installer that orchestrates a productivity-focused Ubuntu 24.04 desktop setup (workspaces, hotkeys, window tiling, programming tools, and more). Tested on Ubuntu 24.04.4 LTS.

Standalone installer and runtime for **LM Studio local-agent serving**: it installs
the LM Studio AppImage + CLI, runs the headless OpenAI-compatible server as a
systemd user service, and manages a registry of local GGUF models with an
interactive installer for choosing which models to install, which stays loaded,
and which the active "main" model switches to.

This subsystem used to live inside
[`agent_command_center`](https://github.com/mikaeltorni/agent_command_center); it
now lives here as the single source of truth for local model serving. Agent
Command Center and `linux_programming_setup` depend on it to provide the models
they consume, but it installs and runs **standalone**.

## Model switching model

Every model in [`configs/lmstudio/model-profiles.json`](configs/lmstudio/model-profiles.json)
carries three install-time fields:

- **`switch_env_var`** — a per-model environment variable. Set it truthy and the
  active served model switches to that model (e.g. `LMSTUDIO_USE_ORNSTEIN=1`).
- **`default_keep_on`** — the model is always kept loaded (it is not part of the
  switchable "main" slot). The small CPU-resident Qwen coder is kept on by
  default so editor autocomplete is always available.
- **`default_main`** — the model the top-level `main_env_var`
  (`LMSTUDIO_MAIN_MODEL`) resolves to, and the default active model when no
  per-model switch variable is set.

On a terminal the `models` component opens an interactive menu where, per model,
you toggle **install** and **autostart** (load at boot), and can **add your own
model** by typing a name + `lms` model key + download URL. Your choices are
written into a customized catalog, so downloads, boot-load, and the per-model
switch commands all follow exactly what you picked — a model you install but do
not autostart is downloaded and runtime-switchable yet not loaded at boot. The
default (non-interactive) install ships these fields ready to go:

| Model | Role | Default |
|---|---|---|
| `qwen-coder` (Qwen2.5 Coder 1.5B) | editor autocomplete | **kept on** (`LMSTUDIO_USE_QWEN_CODER`) |
| `ornstein` (Ornstein 3.6 35B-A3B) | primary coding agent | **main** (`LMSTUDIO_USE_ORNSTEIN`, `LMSTUDIO_MAIN_MODEL`) |

At runtime the active main model is resolved by precedence: the first model whose
`switch_env_var` is truthy, else `LMSTUDIO_MAIN_MODEL` when it names a model, else
`default_main`. Keep-on models are always loaded alongside it.

## Repository dependencies

Installs and runs **standalone**; siblings below only enhance it.

- **Build-time (soft):** the shared installer component framework
  [`linux_installation_scripts_functions`](https://github.com/mikaeltorni/linux_installation_scripts_functions) —
  loaded from a sibling checkout when present, otherwise downloaded on demand.
- **Consumers:** [`agent_command_center`](https://github.com/mikaeltorni/agent_command_center)
  (its local Codex/Qwen agents talk to this server) and
  [`linux_programming_setup`](https://github.com/mikaeltorni/linux_programming_setup)
  (its Continue editor autocomplete depends on this repo to install only the
  `qwen-coder` model). Neither is required for this repo to function.

See the full cross-repository map in
[installation_scripts/DEPENDENCIES.md](https://github.com/mikaeltorni/installation_scripts/blob/master/DEPENDENCIES.md).

## Install

The **core** is just the LM Studio app + the serving service (`lm_studio_app` +
`serve`); it runs no matter what else you pick. Everything model-related is
optional and fully customizable through the `models` component's interactive
menu, and the server starts fine before any model is downloaded (the boot script
skips models that are not present locally).

```bash
git clone https://github.com/mikaeltorni/linux_lmstudio_local_agents.git
cd linux_lmstudio_local_agents
bash install.sh                 # interactive component menu, then per-model menu
bash install.sh --default       # non-interactive: core app/service, no models
bash install.sh --select lm_studio_app,serve,models   # app/service plus model selector
bash install.sh --select serve  # core serving only, no model downloads
```

Installing the LM Studio application itself writes to `/opt/lm-studio` and
`/usr/local/bin`, so that step needs root; run `sudo bash install.sh` for a full
install, or run unprivileged to configure serving and models for an already
installed LM Studio (root-only steps are skipped and reported).

## Components

| id | description | default | role |
|---|---|---|---|
| `lm_studio_app` | LM Studio AppImage + CLI (root) | on | core |
| `serve` | serve.env, boot script, and `lmstudio-serve.service` user service | on | core |
| `models` | Choose models to install + autostart, or add your own (interactive) | off | optional |

`models` is default-off so a default or unattended install does not download or
register any LLM models. Select it in the menu (or pass `--select
lm_studio_app,serve,models`) when model installation is wanted; leave it off for
core serving only.

## Tooling

- `lmstudio-model <id>\|--restore` — switch the active GPU-resident model and
  persist the choice (used by the boot service and the per-model switch vars).
- The model registry and resolution logic — including per-model install/autostart
  selection and custom-model normalization — live in
  [`scripts/lmstudio_models.py`](scripts/lmstudio_models.py) and are unit-tested.
- The interactive per-model install menu lives in
  [`scripts/lmstudio_install_menu.py`](scripts/lmstudio_install_menu.py); its
  selection state is unit-tested with scripted input.

## Detect, reconfigure, and uninstall

This installer tracks what it has installed and can re-apply or remove it, so you
can refresh configuration after a repo update or cleanly back a feature out.

```bash
bash install.sh --detect            # show each component as installed|absent
bash install.sh --reconfigure a,b   # re-apply (idempotent) these component ids
bash install.sh --uninstall a,b     # uninstall these component ids
```

In the interactive menu (run `bash install.sh` on a terminal, or via the master
installer), already-installed components show a green `✓`. Select one with
**space** to **reconfigure** it (`~`), press **`u`** to mark it for **uninstall**
(`✗`), or press **`r`** to reconfigure every installed component at once. Detection
uses a live check where deterministic and otherwise an install receipt under
`${XDG_STATE_HOME:-~/.local/state}/isc/receipts/`; a component without a reversal
step simply clears that receipt on uninstall.

## Disclaimer

This software is provided under the MIT License on an **“as is”** basis, without warranties of any kind. To the maximum extent permitted by applicable law, the authors and copyright holders shall not be liable for any claims, damages, losses, or other liability arising from the use of this software.

You are solely responsible for determining whether this software is suitable, safe, lawful, and appropriate for your intended use. Unless explicitly stated otherwise, this project is general-purpose software and is not designed, tested, certified, or approved for safety-critical, medical, automotive, aviation, industrial-control, life-support, cybersecurity-critical, financial-critical, or other high-risk use cases.

The authors and copyright holders make no guarantees regarding security, reliability, availability, correctness, compliance, non-infringement, or fitness for any particular purpose.

This notice is intended to clarify the nature of the project and does not impose additional restrictions beyond the MIT License.
