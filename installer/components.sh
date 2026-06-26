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
  "lm_studio_app|LM Studio AppImage + CLI (requires root)|on|install_lm_studio_app|detect_lm_studio_app|uninstall_lm_studio_app"
  "models|Choose models to install + autostart, or add your own (interactive)|off|ensure_lmstudio_models_installed|detect_models|uninstall_models"
  "serve|serve.env, boot script, and lmstudio-serve.service user service|on|configure_serving|detect_serve|uninstall_serve"
)

# Exclude large AI model downloads from automatic installation.
ISC_COMPONENTS+=(
  "excluded_nex_model|Excluded Nex AI model (skip download)|off|detect_excluded_nex_model|uninstall_excluded_nex_model"
  "excluded_qwen_large|Excluded large Qwen model (skip download)|off|detect_excluded_qwen_large|uninstall_excluded_qwen"  
  "excluded_ornstein|Excluded Ornstein AI model (skip download)|off|detect_excluded_ornstein|uninstall_excluded_ornstein"
)

# Mark models component as installed when catalog exists and contains models.
ensure_lmstudio_models_installed() {
  local catalog="$TARGET_HOME/.config/lmstudio-local-agents/configs/lmstudio/model-profiles.json"
  if [ -f "$catalog" ] && python3 -c "import json; data=json.load(open('$catalog')); exit(0 if len(data.get('models',[])) > 0 else 1)" 2>/dev/null; then
    _isc_mark_installed "models" || true  
  fi
}

