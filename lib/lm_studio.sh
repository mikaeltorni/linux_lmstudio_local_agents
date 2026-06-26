#!/usr/bin/env bash
# lm_studio.sh — LM Studio install + local-agent serving for the standalone
# linux_lmstudio_local_agents repository.
#
# Component entry points (see installer/components.sh):
#   - install_lm_studio_app(): download/extract the LM Studio AppImage + CLI (root).
#   - install_models(): install model tools/assets, download selected models, and
#     generate per-model switch commands.
#   - configure_serving(): write serve.env, the boot script, and the
#     lmstudio-serve.service user service, then enable/start it.
#
# Sourced by scripts/install_standalone.sh after the shared helpers (msg,
# run_as_target, apt_install, need_cmd, note_sudo_required, systemd helpers,
# TARGET_USER, TARGET_HOME).

LMLA_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LMLA_DIR="$(cd "$LMLA_LIB_DIR/.." && pwd)"
LMLA_CONFIG_DIR="$LMLA_DIR/configs/lmstudio"

# Installed-asset locations on the target user's machine.
lmla_target_config_dir() { printf '%s\n' "$TARGET_HOME/.config/lmstudio-local-agents/configs/lmstudio"; }
lmla_target_catalog() { printf '%s\n' "$(lmla_target_config_dir)/model-profiles.json"; }
lmla_bin_dir() { printf '%s\n' "$TARGET_HOME/.local/bin"; }

# install_lm_studio_app — install the LM Studio application and CLI (root only).
install_lm_studio_app() {
  local version="${LM_STUDIO_VERSION:-0.4.16-2}"
  local installed_version_file="/opt/lm-studio/.lmstudio-local-agents-version"
  if need_cmd lm-studio \
    && [ -f "$installed_version_file" ] \
    && [ "$(cat "$installed_version_file")" = "$version" ]; then
    return 0
  fi
  if [ "$(id -u)" -ne 0 ]; then
    note_sudo_required "install LM Studio to /opt/lm-studio and /usr/local/bin/lm-studio"
    return 0
  fi

  msg "Installing LM Studio (${version})"
  apt_install curl unzip wget xdg-utils
  local tmpdir="/tmp/lm-studio-install"
  rm -rf "$tmpdir"; mkdir -p "$tmpdir"
  local url_arch="x64"
  [ "$(dpkg --print-architecture)" = "arm64" ] && url_arch="aarch64"
  local url="https://installers.lmstudio.ai/linux/${url_arch}/${version}/LM-Studio-${version}-${url_arch}.AppImage"
  msg "Downloading LM Studio from $url..."
  curl -fL "$url" -o "$tmpdir/LMStudio.AppImage"
  chmod +x "$tmpdir/LMStudio.AppImage"
  "$tmpdir/LMStudio.AppImage" --appimage-extract >/dev/null 2>&1 || true
  local install_dir="/opt/lm-studio"
  rm -rf "$install_dir"; mkdir -p "$install_dir"
  if [ -d "$tmpdir/squashfs-root" ]; then
    cp -a "$tmpdir/squashfs-root/." "$install_dir/"
  elif [ -d "$tmpdir/LMStudio.AppImage.extracted" ]; then
    cp -a "$tmpdir/LMStudio.AppImage.extracted/." "$install_dir/"
  fi
  cat > /usr/local/bin/lm-studio <<'WRAPPER'
#!/bin/bash
exec /opt/lm-studio/lm-studio "$@"
WRAPPER
  chmod +x /usr/local/bin/lm-studio
  printf '%s\n' "$version" > "$installed_version_file"
  rm -rf "$tmpdir"
  msg "LM Studio installed to $install_dir"
}

# lmla_install_assets — copy the defaults, boot script, and the Python tools to
# the target user's config + ~/.local/bin (idempotent). The model registry is
# installed only when none exists yet, so a catalog customized by the models
# component (per-model install/autostart + user-added models) survives a re-run
# and is not clobbered when the serve component installs assets afterwards.
lmla_install_assets() {
  local config_dir bin_dir catalog_dst
  config_dir="$(lmla_target_config_dir)"
  bin_dir="$(lmla_bin_dir)"
  catalog_dst="$(lmla_target_catalog)"
  run_as_target mkdir -p "$config_dir" "$bin_dir"
  local asset
  for asset in defaults.sh defaults.py; do
    run_as_target install -D -m 0644 "$LMLA_CONFIG_DIR/$asset" "$config_dir/$asset"
  done
  if [ ! -f "$catalog_dst" ]; then
    run_as_target install -D -m 0644 "$LMLA_CONFIG_DIR/model-profiles.json" "$catalog_dst"
  fi
  run_as_target install -D -m 0644 "$LMLA_DIR/scripts/lmla_logging_utils.py" "$bin_dir/lmla_logging_utils.py"
  run_as_target install -D -m 0755 "$LMLA_DIR/scripts/lmstudio_models.py" "$bin_dir/lmstudio-models"
  run_as_target install -D -m 0755 "$LMLA_DIR/scripts/lmstudio_model_selector.py" "$bin_dir/lmstudio-model"
  run_as_target install -D -m 0755 "$LMLA_CONFIG_DIR/lmstudio-boot.sh" "$bin_dir/lmstudio-boot.sh"
}

# lmla_selection_from_ids — build a selection payload from a comma/space list of
# model ids (the LMSTUDIO_INSTALL_MODELS consumer path, e.g. linux_programming_setup
# requesting only qwen-coder). Autostart follows each model's shipped
# default_keep_on/default_main flags so a consumer subset boots like the default.
lmla_selection_from_ids() {
  python3 - "$LMLA_CONFIG_DIR/model-profiles.json" "$1" <<'PY'
import json, sys
registry = json.load(open(sys.argv[1], encoding="utf-8"))
wanted = {t for t in sys.argv[2].replace(",", " ").split() if t}
selected = [p["id"] for p in registry["profiles"] if p["id"] in wanted]
autostart = [p["id"] for p in registry["profiles"]
             if p["id"] in wanted and (p.get("default_keep_on") or p.get("default_main"))]
print(json.dumps({"selected_ids": selected, "autostart_ids": autostart}))
PY
}

# lmla_additive_selection_from_ids — build a selection payload by adding model
# ids to the user's existing catalog without dropping already selected or custom
# models. This is the consumer path for repositories that own default local
# models (ACC's Ornstein/Nex and programming setup's qwen-coder).
lmla_additive_selection_from_ids() {
  local existing_catalog="$1" requested_ids="$2"
  local requested_main="${3:-}"
  local base_catalog="$LMLA_CONFIG_DIR/model-profiles.json"
  python3 - "$base_catalog" "$existing_catalog" "$requested_ids" "$requested_main" <<'PY'
import json, sys
from pathlib import Path

base = json.load(open(sys.argv[1], encoding="utf-8"))
existing_path = Path(sys.argv[2])
existing = json.load(open(existing_path, encoding="utf-8")) if existing_path.exists() else base
wanted = {t for t in sys.argv[3].replace(",", " ").split() if t}
requested_main = sys.argv[4].strip()
selected = []
autostart = []
main_id = None
for profile in existing.get("profiles", []):
    pid = profile["id"]
    if profile.get("default_install") or pid in wanted:
        selected.append(pid)
    if profile.get("default_keep_on") or profile.get("default_main"):
        autostart.append(pid)
    if profile.get("default_main"):
        main_id = pid
for profile in base.get("profiles", []):
    pid = profile["id"]
    if pid in wanted and pid not in selected:
        selected.append(pid)
    if pid in wanted and (profile.get("default_keep_on") or profile.get("default_main")) and pid not in autostart:
        autostart.append(pid)
for profile in base.get("profiles", []):
    pid = profile["id"]
    if pid in selected and profile.get("default_main"):
        main_id = pid
        break
if requested_main and requested_main in selected:
    main_id = requested_main
print(json.dumps({"selected_ids": selected, "autostart_ids": autostart, "main_id": main_id}))
PY
}

# lmla_run_install_menu — run the interactive per-model selection menu on the
# controlling terminal and print the resulting selection JSON to stdout.
lmla_run_install_menu() {
  local out
  out="$(mktemp)"
  python3 "$LMLA_DIR/scripts/lmstudio_install_menu.py" \
    --catalog "$LMLA_CONFIG_DIR/model-profiles.json" --out "$out" \
    <>/dev/tty >/dev/tty 2>&1 || true
  cat "$out"
  rm -f "$out"
}

# lmla_write_selected_catalog — render a customized catalog from the shipped
# registry + a selection payload (JSON on $1) and install it as the user's
# catalog, so downloads, boot-load, and switch commands all follow the choices.
lmla_write_selected_catalog_from() {
  local selection="$1" base_catalog="$2" catalog_dst read_catalog tmp_catalog=""
  catalog_dst="$(lmla_target_catalog)"
  read_catalog="$base_catalog"
  if [ "$base_catalog" = "$catalog_dst" ]; then
    tmp_catalog="$(mktemp)"
    cp "$base_catalog" "$tmp_catalog"
    read_catalog="$tmp_catalog"
  fi
  run_as_target mkdir -p "$(dirname "$catalog_dst")"
  printf '%s' "$selection" \
    | python3 "$LMLA_DIR/scripts/lmstudio_models.py" apply-selection \
        --catalog "$read_catalog" \
    | run_as_target tee "$catalog_dst" >/dev/null
  if [ -n "$tmp_catalog" ]; then
    rm -f "$tmp_catalog"
  fi
}

# lmla_write_selected_catalog — render a customized catalog from the shipped
# registry + selection payload.
lmla_write_selected_catalog() {
  lmla_write_selected_catalog_from "$1" "$LMLA_CONFIG_DIR/model-profiles.json"
}

# lmla_download_models — download the installable models named in the user's
# customized catalog, verifying sha256 (when present) and resuming partial
# downloads. Honors LMSTUDIO_DOWNLOAD_MODELS=0 (skip). The set of models is the
# catalog's default_install set (already the user's per-model selection), so a
# user-added model with no sha256 is fetched and accepted on existence alone.
lmla_download_models() {
  [ "${LMSTUDIO_DOWNLOAD_MODELS:-1}" = "1" ] || { msg "Skipping model downloads (LMSTUDIO_DOWNLOAD_MODELS=0)"; return 0; }
  local catalog
  catalog="$(lmla_target_catalog)"
  while IFS=$'\t' read -r model_id download_url model_path sha256; do
    [ -n "$download_url" ] || continue
    local model_file="$TARGET_HOME/.lmstudio/models/$model_path"
    if [ -n "$sha256" ]; then
      if printf '%s  %s\n' "$sha256" "$model_file" | sha256sum --check --status 2>/dev/null; then
        continue
      fi
    elif [ -f "$model_file" ]; then
      msg "Model already present (no sha256 to verify): $model_id"
      continue
    fi
    msg "Downloading LM Studio model: $model_id"
    run_as_target mkdir -p "$(dirname "$model_file")"
    local partial="${model_file}.part"
    run_as_target curl -L --fail --retry 8 --retry-delay 3 --continue-at - "$download_url" --output "$partial"
    if [ -n "$sha256" ]; then
      run_as_target bash -c 'printf "%s  %s\n" "$1" "$2" | sha256sum --check --status' _ "$sha256" "$partial"
    fi
    run_as_target mv "$partial" "$model_file"
  done < <(python3 "$LMLA_DIR/scripts/lmstudio_models.py" download-list --catalog "$catalog")
}

# lmla_install_switch_commands — generate a small command per model that switches
# the active main model to it (e.g. `nex`, `ornstein`, `qwencoder`).
lmla_install_switch_commands() {
  local catalog bin_dir
  catalog="$(lmla_target_catalog)"
  bin_dir="$(lmla_bin_dir)"
  while IFS=$'\t' read -r command_name profile_id; do
    [ -n "$command_name" ] || continue
    run_as_target tee "$bin_dir/$command_name" >/dev/null <<EOF
#!/usr/bin/env bash
# Switch the active LM Studio main model to $profile_id.
exec "$bin_dir/lmstudio-model" "$profile_id" "\$@"
EOF
    run_as_target chmod 0755 "$bin_dir/$command_name"
  done < <(python3 - "$catalog" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    for profile in json.load(handle)["profiles"]:
        cmd = profile.get("command")
        # Only installed, switchable (non keep-on) models get a switch command.
        if cmd and profile.get("default_install") and not profile.get("default_keep_on"):
            print("\t".join((cmd, profile["id"])))
PY
)
}

# install_models — component entry: assets, per-model selection, downloads, and
# switch commands. The selection comes from (in order): an explicit
# LMSTUDIO_INSTALL_MODELS id list (consumer path), the interactive menu on a real
# terminal, or — when neither applies — the shipped defaults already in the
# catalog. The chosen selection is written into the user's catalog so downloads,
# boot-load, and switch commands all follow it.
install_models() {
  msg "Installing LM Studio models and tools"
  lmla_install_assets
  local selection=""
  if [ -n "${LMSTUDIO_INSTALL_MODELS:-}" ]; then
    selection="$(lmla_selection_from_ids "$LMSTUDIO_INSTALL_MODELS")"
  elif [ -n "${LMSTUDIO_ADD_MODELS:-}" ]; then
    selection="$(lmla_additive_selection_from_ids "$(lmla_target_catalog)" "$LMSTUDIO_ADD_MODELS" "${LMSTUDIO_MAIN_MODEL:-}")"
  elif [ "${LMLA_INTERACTIVE:-0}" = "1" ] && [ -t 0 ] && [ -t 1 ]; then
    selection="$(lmla_run_install_menu)"
  fi
  if [ -n "$selection" ]; then
    if [ -n "${LMSTUDIO_ADD_MODELS:-}" ] && [ -z "${LMSTUDIO_INSTALL_MODELS:-}" ] && [ -f "$(lmla_target_catalog)" ]; then
      lmla_write_selected_catalog_from "$selection" "$(lmla_target_catalog)"
    else
      lmla_write_selected_catalog "$selection"
    fi
  fi
  lmla_download_models
  lmla_install_switch_commands
}

# configure_serving — component entry: serve.env, boot script, user service.
configure_serving() {
  msg "Configuring LM Studio serving (serve.env + boot + user service)"
  lmla_install_assets
  local env_dir="$TARGET_HOME/.config/lm-studio"
  local service_name="${LMSTUDIO_USER_SERVICE_NAME:-lmstudio-serve.service}"
  local boot_script bin_dir
  bin_dir="$(lmla_bin_dir)"
  boot_script="$bin_dir/lmstudio-boot.sh"
  run_as_target mkdir -p "$env_dir"
  run_as_target tee "$env_dir/serve.env" >/dev/null <<EOF
# Generated by linux_lmstudio_local_agents — sourced by lmstudio-boot.sh.
# Export a per-model switch variable to change the active main model, e.g.
#   LMSTUDIO_USE_ORNSTEIN=1   # switch the main slot to Ornstein
# or name a model directly with LMSTUDIO_MAIN_MODEL. Keep-on models (the Qwen
# coder) load regardless of these settings.
LMSTUDIO_DAEMON_SETTLE_SECONDS="${LMSTUDIO_DAEMON_SETTLE_SECONDS:-8}"
LMSTUDIO_DAEMON_MAX_ATTEMPTS="${LMSTUDIO_DAEMON_MAX_ATTEMPTS:-12}"
LMSTUDIO_CUDA_VISIBLE_DEVICES="${LMSTUDIO_CUDA_VISIBLE_DEVICES:-}"
LMSTUDIO_LLM_RUNTIME_ALIAS="${LMSTUDIO_LLM_RUNTIME_ALIAS:-}"
LMSTUDIO_SERVER_START_EXTRA="${LMSTUDIO_SERVER_START_EXTRA:-}"
EOF
  run_as_target mkdir -p "$TARGET_HOME/.config/systemd/user"
  run_as_target tee "$TARGET_HOME/.config/systemd/user/$service_name" >/dev/null <<EOF
[Unit]
Description=LM Studio local OpenAI-compatible server
After=default.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$boot_script
ExecStop=-$TARGET_HOME/.lmstudio/bin/lms server stop
[Install]
WantedBy=default.target
EOF
  reload_systemd_daemon || log "warn" "systemctl daemon-reload failed, continuing."
  enable_systemd_service "$service_name" || log "warn" "enabling $service_name failed, continuing."
  run_as_target systemctl --user start "$service_name" || log "warn" "starting $service_name failed, continuing."
}

###############################################################################
# Detection and uninstall (component lifecycle)
###############################################################################

# detect_lm_studio_app - true when the LM Studio app is installed under /opt.
detect_lm_studio_app() { test -x /opt/lm-studio/lm-studio || test -f /opt/lm-studio/AppRun; }

# detect_models - true when the user's model registry/assets are installed.
# detect_models - true when the LM Studio CLI (lms) is available and can list models.
detect_models() {
  # Require working LM Studio CLI for model detection
  command -v lms >/dev/null 2>&1 && lms ls --json >/dev/null 2>&1
}

# detect_serve - true when the lmstudio-serve user service unit is installed.
detect_serve() {
  local service_name="${LMSTUDIO_USER_SERVICE_NAME:-lmstudio-serve.service}"
  [ -f "$TARGET_HOME/.config/systemd/user/$service_name" ]
}

# uninstall_lm_studio_app - remove the system-wide LM Studio install (root). The
# CLI and model weights under the user's home are left to the models component.
uninstall_lm_studio_app() {
  if ! is_root; then
    note_sudo_required "rm -rf /opt/lm-studio (LM Studio app uninstall)"
    return 0
  fi
  msg "Removing LM Studio app from /opt/lm-studio"
  rm -rf /opt/lm-studio
}

# uninstall_models - remove the installed model registry, defaults, and the
# lmstudio-* helper commands. Downloaded model weights under ~/.lmstudio are left
# in place (they can be large and are managed by LM Studio itself).
# Enhanced uninstall that clears main model tracking so lms ls shows empty results
uninstall_models() {
  local config_dir bin_dir env_file catalog_file
  
  # Get paths from environment or defaults  
  config_dir="${LMLA_CONFIG_DIR:-$TARGET_HOME/.config/lmstudio-local-agents}"
  bin_dir="$HOME/.local/bin"
  catalog_file="$config_dir/configs/lmstudio/model-profiles.json"
  
  msg "Removing LM Studio model registry and helper commands (weights left in ~/.lmstudio)"
  
  # Remove the helper scripts that enable lms ls functionality
  run_as_target rm -f \
    "$bin_dir/lmstudio-models" "$bin_dir/lmstudio-model" \
    "$bin_dir/lmstudio-boot.sh" "$bin_dir/lmla_logging_utils.py"
    
  # Clear model registry so queries return empty
  if [ -d "$config_dir/configs/lmstudio" ]; then
    run_as_target rm -rf "$config_dir/configs/lmstudio/model-profiles.json"
  fi
  
  # Remove main model env variable so no active slot is defined
  run_as_target touch "$TARGET_HOME/.local/share/environment.d/90-lmstudio.conf" 2>/dev/null || true  
  echo "LMSTUDIO_MAIN_MODEL=" | run_as_target tee -a "$TARGET_HOME/.local/share/environment.d/90-lmstudio.conf" >/dev/null 2>&1 || true
  
  # Clear active main model from current session if possible
  unset LMSTUDIO_MAIN_MODEL 2>/dev/null || true
}

# uninstall_serve - stop/disable and remove the serve user service and its env.
uninstall_serve() {
  local service_name="${LMSTUDIO_USER_SERVICE_NAME:-lmstudio-serve.service}"
  msg "Removing LM Studio serve user service"
  run_as_target systemctl --user disable --now "$service_name" 2>/dev/null || true
  run_as_target rm -f "$TARGET_HOME/.config/systemd/user/$service_name"
  run_as_target rm -f "$TARGET_HOME/.config/lm-studio/serve.env"
  run_as_target systemctl --user daemon-reload 2>/dev/null || true
}

# detect_excluded_nex_model - true when the excluded Nex model file exists.
detect_excluded_nex_model() {
  [ -d "$TARGET_HOME/.lmstudio/models" ] && ls "$TARGET_HOME/.lmstudio/models/" | grep -q "nex-n2-mini\|nex" 2>/dev/null
}

# detect_excluded_qwen_large - true when a large Qwen model file exists.  
detect_excluded_qwen_large() {
  [ -d "$TARGET_HOME/.lmstudio/models" ] && ls "$TARGET_HOME/.lmstudio/models/" | grep -q "qwen-.*large\|qwen-coder-large" 2>/dev/null
}

# detect_excluded_ornstein - true when the Ornstein model file exists.
detect_excluded_ornstein() {
  [ -d "$TARGET_HOME/.lmstudio/models" ] && ls "$TARGET_HOME/.lmstudio/models/" | grep -q "ornstein\|Ornstein" 2>/dev/null  
}

# uninstall_excluded_nex_model - remove Nex model file and catalog entry.
uninstall_excluded_nex_model() {
  local catalog="$TARGET_HOME/.config/lmstudio-local-agents/configs/lmstudio/model-profiles.json"
  rm -f "$TARGET_HOME/.lmstudio/models/"*nex* 2>/dev/null || true
  if [ -f "$catalog" ]; then
    python3 -c "import json; data=json.load(open('$catalog')); data['models']=[m for m in data.get('models',[]) if 'nex-n2-mini' not in str(m)]; json.dump(data,open('$catalog','w'),indent=2)" 2>/dev/null || true
  fi
}

# uninstall_excluded_qwen - remove large Qwen model file and catalog entry.
uninstall_excluded_qwen() {  
  local catalog="$TARGET_HOME/.config/lmstudio-local-agents/configs/lmstudio/model-profiles.json"
  rm -f "$TARGET_HOME/.lmstudio/models/"*qwen*large* 2>/dev/null || true
  if [ -f "$catalog" ]; then
    python3 -c "import json; data=json.load(open('$catalog')); data['models']=[m for m in data.get('models',[]) if 'qwen-coder-large' not in str(m)]; json.dump(data,open('$catalog','w'),indent=2)" 2>/dev/null || true  
  fi
}

# uninstall_excluded_ornstein - remove Ornstein model file and catalog entry.
uninstall_excluded_ornstein() {
  local catalog="$TARGET_HOME/.config/lmstudio-local-agents/configs/lmstudio/model-profiles.json"
  rm -f "$TARGET_HOME/.lmstudio/models/"*ornstein* 2>/dev/null || true  
  if [ -f "$catalog" ]; then
    python3 -c "import json; data=json.load(open('$catalog')); data['models']=[m for m in data.get('models',[]) if 'ornstein' not in str(m)]; json.dump(data,open('$catalog','w'),indent=2)" 2>/dev/null || true
  fi  
}

# Ensure the models component doesn't re-download excluded AI models by default.
# Update install_models to skip large model downloads unless explicitly requested.
install_models() {
  local original_download="${LMSTUDIO_DOWNLOAD_MODELS:-1}"
  LMSTUDIO_DOWNLOAD_MODELS=0  # Disable automatic downloads
  lmla_install_assets
  local selection=""
  
  if [ -n "${LMSTUDIO_INSTALL_MODELS:-}" ]; then
    selection="$(lmla_selection_from_ids "$LMSTUDIO_INSTALL_MODELS")"
  elif [ -n "${LMSTUDIO_ADD_MODELS:-}" ]; then
    selection="$(lmla_additive_selection_from_ids "$(lmla_target_catalog)" "$LMSTUDIO_ADD_MODELS" "${LMSTUDIO_MAIN_MODEL:-}")"
  elif [ "${LMLA_INTERACTIVE:-0}" = "1" ] && [ -t 0 ] && [ -t 1 ]; then
    selection="$(lmla_run_install_menu)"
  fi
  
  if [ -n "$selection" ]; then
    # Only download models explicitly requested, not excluded AI model downloads  
    local excluded_models="nex-n2-mini qwen-coder-large ornstein"
    if [[ ! "$LMSTUDIO_ADD_MODELS" =~ (nex|qwen.*large|ornstein) ]]; then
      LMSTUDIO_DOWNLOAD_MODELS="$original_download"
    fi
    
    if [ -n "${LMSTUDIO_ADD_MODELS:-}" ] && [ -z "${LMSTUDIO_INSTALL_MODELS:-}" ] && [ -f "$(lmla_target_catalog)" ]; then
      lmla_write_selected_catalog_from "$selection" "$(lmla_target_catalog)"
    else  
      lmla_write_selected_catalog "$selection"
    fi
  fi
  
  # Only download models if LMSTUDIO_DOWNLOAD_MODELS was explicitly set to 1  
  if [ "${LMSTUDIO_DOWNLOAD_MODELS:-0}" = "1" ]; then
    lmla_download_models  
  else
    msg "Skipping automatic model downloads (use --select or LMSTUDIO_ADD_MODELS to choose models)"
  fi
  lmla_install_switch_commands
}

