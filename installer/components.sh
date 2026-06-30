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

ISC_POSTFLIGHT="report_sudo_required"

ISC_COMPONENTS=(
  "models|Choose models to install + autostart, or add your own (interactive)|off|install_models|detect_models|uninstall_models|Models"
  "lm_studio_app|LM Studio AppImage + CLI (requires root)|on|install_lm_studio_app|detect_lm_studio_app|uninstall_lm_studio_app|Applications"
  "serve|serve.env, boot script, and lmstudio-serve.service user service|on|configure_serving|detect_serve|uninstall_serve|Serving"
)

# lmla_list_startup_components - Expose the LM Studio GUI to the master [S]
# selector without starting it by default; serving is owned by the user service.
# Output: component id<TAB>desktop candidates<TAB>default selection.
lmla_list_startup_components() {
  printf 'lm_studio_app\tlm-studio.desktop|lmstudio.desktop\toff\n'
}
