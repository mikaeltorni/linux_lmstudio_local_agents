#!/usr/bin/env bash
# components.sh - Component manifest for linux_lmstudio_local_agents.
#
# Maps each component to the function (defined in lib/lm_studio.sh, sourced by
# scripts/install_standalone.sh) that installs it. The standalone installer
# sources the lib and this manifest, then routes execution through component_main
# from component_runtime.sh.
#
# Entry format: "id|label|default(on/off)|function".

ISC_REPO_NAME="linux_lmstudio_local_agents"
ISC_REPO_LABEL="LM Studio local agents"

# The LM Studio app/CLI, the shared runtime assets, and the lmstudio-serve user
# service are this repository's core: nothing here works without them, so they
# must install even for `--select ''`. They therefore run through ISC_CORE
# rather than as ISC_COMPONENTS rows, which are always deselectable ("on" means
# pre-selected, not mandatory). See the framework README, "Core vs. optional
# components". Do NOT express the core as ISC_PREFLIGHT: the preflight fires
# only in front of the first component that actually runs, so a blank selection
# would skip it.
ISC_CORE="install_lmstudio_core"

ISC_POSTFLIGHT="report_sudo_required"

# Only genuinely optional choices belong here. `lm_studio_app` stays listed
# because the row carries the master installer's [S] GUI-autostart toggle (see
# lmla_list_startup_components, which is only honored for ids that are also
# component rows) and its [u] uninstall action — the app itself is installed by
# the core above, so deselecting the row cannot leave the repository broken.
ISC_COMPONENTS=(
  "models|Choose models to install + autostart, or add your own|off|install_models|detect_models|uninstall_models|Models||configure_models"
  "lm_studio_app|LM Studio GUI autostart + app refresh (app installed by core)|on|install_lm_studio_app|detect_lm_studio_app|uninstall_lm_studio_app|Applications"
)

# lmla_list_startup_components - Expose the LM Studio GUI to the master [S]
# selector without starting it by default; serving is owned by the user service.
# Output: component id<TAB>desktop candidates<TAB>default selection.
lmla_list_startup_components() {
  printf 'lm_studio_app\tlm-studio.desktop|lmstudio.desktop\toff\n'
}
