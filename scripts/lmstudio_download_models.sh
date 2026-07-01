#!/usr/bin/env bash
# lmstudio_download_models.sh - Resumable download of the catalog's selected
# models, run as the target user.
#
# Invoked two ways:
#   * directly/synchronously by the installer's lmla_download_models, and
#   * by the lmstudio-model-download.service systemd --user unit, which re-runs
#     it on every login so a multi-gigabyte model finishes across reboots without
#     blocking the installer.
#
# It is idempotent: models already present (sha256-verified when a checksum is
# known, otherwise by existence) are skipped, and partial downloads resume via
# `curl --continue-at -`. Exits 0 even when a download is incomplete so the
# oneshot service is not marked failed; the next run resumes it.
set -uo pipefail

CATALOG="${LMSTUDIO_CATALOG:-$HOME/.config/lmstudio-local-agents/configs/lmstudio/model-profiles.json}"
MODELS_DIR="${LMSTUDIO_MODELS_DIR:-$HOME/.lmstudio/models}"
MODELS_CLI="${LMSTUDIO_MODELS_CLI:-$HOME/.local/bin/lmstudio-models}"

log() { printf '[%(%Y-%m-%dT%H:%M:%S%z)T] [lmstudio-download] %s\n' -1 "$*"; }

[ -f "$CATALOG" ] || { log "no catalog at $CATALOG; nothing to download"; exit 0; }
if [ ! -x "$MODELS_CLI" ]; then
  if command -v lmstudio-models >/dev/null 2>&1; then
    MODELS_CLI="$(command -v lmstudio-models)"
  else
    log "lmstudio-models CLI not found; cannot resolve download list"
    exit 0
  fi
fi

pending=0
while IFS=$'\t' read -r model_id download_url download_source model_path sha256; do
  if [ -n "${download_source:-}" ]; then
    log "downloading model through LM Studio: $model_id ($download_source)"
    if ! PATH="$HOME/.lmstudio/bin:$HOME/.local/bin:$PATH" lms get --gguf --yes "$download_source"; then
      log "LM Studio download incomplete for $model_id; will retry on the next run"
      pending=1
    fi
    continue
  fi
  [ -n "${download_url:-}" ] || continue
  model_file="$MODELS_DIR/$model_path"
  if [ -n "${sha256:-}" ]; then
    if printf '%s  %s\n' "$sha256" "$model_file" | sha256sum --check --status 2>/dev/null; then
      continue
    fi
  elif [ -f "$model_file" ]; then
    continue
  fi
  log "downloading model: $model_id"
  mkdir -p "$(dirname "$model_file")"
  partial="${model_file}.part"
  if ! curl -L --fail --retry 8 --retry-delay 3 --continue-at - "$download_url" --output "$partial"; then
    log "download incomplete for $model_id; will resume on the next run"
    pending=1
    continue
  fi
  if [ -n "${sha256:-}" ]; then
    if ! printf '%s  %s\n' "$sha256" "$partial" | sha256sum --check --status; then
      log "checksum mismatch for $model_id; leaving partial file for re-download"
      pending=1
      continue
    fi
  fi
  mv "$partial" "$model_file"
  log "completed model: $model_id"
done < <("$MODELS_CLI" download-list --catalog "$CATALOG")

if [ "$pending" = "1" ]; then
  log "one or more models still pending; the service will retry on the next login"
fi
exit 0
