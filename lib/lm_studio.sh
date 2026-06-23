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
exec /opt/lm-studio/LMStudio "$@"
WRAPPER
  chmod +x /usr/local/bin/lm-studio
  printf '%s\n' "$version" > "$installed_version_file"
  rm -rf "$tmpdir"
  msg "LM Studio installed to $install_dir"
}

# lmla_install_assets — copy the model registry, defaults, boot script, and the
# Python tools to the target user's config + ~/.local/bin (idempotent).
lmla_install_assets() {
  local config_dir bin_dir
  config_dir="$(lmla_target_config_dir)"
  bin_dir="$(lmla_bin_dir)"
  run_as_target mkdir -p "$config_dir" "$bin_dir"
  local asset
  for asset in model-profiles.json defaults.sh defaults.py; do
    run_as_target install -D -m 0644 "$LMLA_CONFIG_DIR/$asset" "$config_dir/$asset"
  done
  run_as_target install -D -m 0644 "$LMLA_DIR/scripts/lmla_logging_utils.py" "$bin_dir/lmla_logging_utils.py"
  run_as_target install -D -m 0755 "$LMLA_DIR/scripts/lmstudio_models.py" "$bin_dir/lmstudio-models"
  run_as_target install -D -m 0755 "$LMLA_DIR/scripts/lmstudio_model_selector.py" "$bin_dir/lmstudio-model"
  run_as_target install -D -m 0755 "$LMLA_CONFIG_DIR/lmstudio-boot.sh" "$bin_dir/lmstudio-boot.sh"
}

# lmla_download_models — download the installable models, verifying sha256 and
# resuming partial downloads. Honors LMSTUDIO_DOWNLOAD_MODELS=0 (skip) and
# LMSTUDIO_INSTALL_MODELS (a comma/space list of ids — e.g. a consumer that wants
# only the qwen-coder model). The model selection is computed by the tested
# lmstudio_models.py download-list command.
lmla_download_models() {
  [ "${LMSTUDIO_DOWNLOAD_MODELS:-1}" = "1" ] || { msg "Skipping model downloads (LMSTUDIO_DOWNLOAD_MODELS=0)"; return 0; }
  local catalog="$LMLA_CONFIG_DIR/model-profiles.json"
  while IFS=$'\t' read -r model_id download_url model_path sha256; do
    [ -n "$download_url" ] || continue
    local model_file="$TARGET_HOME/.lmstudio/models/$model_path"
    if printf '%s  %s\n' "$sha256" "$model_file" | sha256sum --check --status 2>/dev/null; then
      continue
    fi
    msg "Downloading LM Studio model: $model_id"
    run_as_target mkdir -p "$(dirname "$model_file")"
    local partial="${model_file}.part"
    run_as_target curl -L --fail --retry 8 --retry-delay 3 --continue-at - "$download_url" --output "$partial"
    run_as_target bash -c 'printf "%s  %s\n" "$1" "$2" | sha256sum --check --status' _ "$sha256" "$partial"
    run_as_target mv "$partial" "$model_file"
  done < <(LMSTUDIO_INSTALL_MODELS="${LMSTUDIO_INSTALL_MODELS:-}" \
    python3 "$LMLA_DIR/scripts/lmstudio_models.py" download-list --catalog "$catalog")
}

# lmla_install_switch_commands — generate a small command per model that switches
# the active main model to it (e.g. `nex`, `ornstein`, `qwencoder`).
lmla_install_switch_commands() {
  local catalog="$LMLA_CONFIG_DIR/model-profiles.json" bin_dir
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
        if cmd and not profile.get("default_keep_on"):
            print("\t".join((cmd, profile["id"])))
PY
)
}

# install_models — component entry: assets + downloads + switch commands.
install_models() {
  msg "Installing LM Studio models and tools"
  lmla_install_assets
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
