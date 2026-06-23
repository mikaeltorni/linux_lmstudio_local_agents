#!/usr/bin/env bash
# lmstudio-boot.sh — start the LM Studio headless daemon + OpenAI-compatible
# server, then load the keep-on models and the resolved active main model.
#
# Managed by linux_lmstudio_local_agents. The set of models to load is decided by
# scripts/lmstudio_models.py (boot-load), which reads the per-model switch
# variables (LMSTUDIO_USE_*) and LMSTUDIO_MAIN_MODEL from the environment, so the
# active main model follows whatever the user exported.
set -euo pipefail

CONFIG_HOME="${HOME}/.config/lmstudio-local-agents"
CATALOG="${LMSTUDIO_MODEL_CATALOG:-$CONFIG_HOME/configs/lmstudio/model-profiles.json}"
MODELS_PY="${LMSTUDIO_MODELS_PY:-$HOME/.local/bin/lmstudio-models}"

# Canonical serving defaults (fallbacks when serve.env is absent). Sourced before
# PATH is built so LMSTUDIO_BIN_DIR (the single source of truth for the lms CLI
# location) is honored everywhere below.
# shellcheck source=/dev/null
[ -f "$CONFIG_HOME/configs/lmstudio/defaults.sh" ] && . "$CONFIG_HOME/configs/lmstudio/defaults.sh"
LMSTUDIO_BIN_DIR="${LMSTUDIO_BIN_DIR:-$HOME/.lmstudio/bin}"
export PATH="${LMSTUDIO_BIN_DIR}:${HOME}/.local/bin:/usr/bin:/bin"
# Optional editor autocomplete profile owned by linux_programming_setup.
# shellcheck source=/dev/null
[ -f "$HOME/.config/linux-programming-setup/lmstudio/autocomplete.env" ] && \
  . "$HOME/.config/linux-programming-setup/lmstudio/autocomplete.env"
# serve.env carries the user's exported switch variables for this service.
ENV_FILE="${HOME}/.config/lm-studio/serve.env"
# shellcheck source=/dev/null
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
SELECTED_MODEL_FILE="${HOME}/.config/lm-studio/selected-model"

# Restore the last runtime selection on boot, unless serve.env explicitly set a
# switch variable or LMSTUDIO_MAIN_MODEL. lmstudio-model persists profile ids,
# command aliases, or model keys; lmstudio-models resolves all of those.
if [ -z "${LMSTUDIO_MAIN_MODEL:-}" ] && [ -f "$SELECTED_MODEL_FILE" ]; then
  if ! env | grep -Eq '^LMSTUDIO_USE_[A-Z0-9_]+=.+$'; then
    LMSTUDIO_MAIN_MODEL="$(tr -d '[:space:]' < "$SELECTED_MODEL_FILE")"
    export LMSTUDIO_MAIN_MODEL
  fi
fi

LMSTUDIO_DAEMON_SETTLE_SECONDS="${LMSTUDIO_DAEMON_SETTLE_SECONDS:-8}"
if [ -n "${LMSTUDIO_CUDA_VISIBLE_DEVICES:-}" ]; then
  export CUDA_VISIBLE_DEVICES="${LMSTUDIO_CUDA_VISIBLE_DEVICES}"
fi

LMS_CLI="${LMSTUDIO_BIN_DIR}/lms"
if [ ! -x "$LMS_CLI" ]; then
  echo "lms not found in PATH (expected ${LMS_CLI})" >&2
  exit 1
fi
if [ -n "${LMSTUDIO_LLM_RUNTIME_ALIAS:-}" ]; then
  "$LMS_CLI" runtime select "${LMSTUDIO_LLM_RUNTIME_ALIAS}" >/dev/null 2>&1 || true
fi

restart_lmstudio_daemon() {
  pkill -u "$(id -un)" -f '(^|/)llmster( |$)' || true
  sleep 2
  "$LMS_CLI" daemon up >/dev/null 2>&1 || true
  sleep "${LMSTUDIO_DAEMON_SETTLE_SECONDS}"
  if ! "$LMS_CLI" ps --json >/dev/null 2>&1; then
    echo "[boot] LM Studio headless daemon failed to start" >&2
    exit 1
  fi
}

start_lmstudio_server() {
  local output status
  output="$("$LMS_CLI" server start 2>&1)" && return 0
  status=$?
  if printf '%s\n' "$output" | grep -qi "Invalid passkey"; then
    restart_lmstudio_daemon
    "$LMS_CLI" server start >/dev/null 2>&1
    return $?
  fi
  printf '%s\n' "$output" >&2
  return "$status"
}

model_key_exists() {
  "$LMS_CLI" ls --json 2>/dev/null | grep -Fq "\"modelKey\":\"$1\""
}

resolve_load_key() {
  local model="$1" short="${1##*/}"
  if model_key_exists "$model"; then printf '%s\n' "$model"
  elif [ "$short" != "$model" ] && model_key_exists "$short"; then printf '%s\n' "$short"
  else printf '%s\n' "$model"; fi
}

restart_lmstudio_daemon
for _ in 1 2 3 4 5; do start_lmstudio_server && break; sleep 3; done

# Load each model the resolver selected (keep-on models, then the active main).
if [ -x "$MODELS_PY" ] || command -v python3 >/dev/null 2>&1; then
  while IFS=$'\t' read -r model_key context gpu parallel ttl identifier; do
    [ -n "$model_key" ] || continue
    if ! model_key_exists "$model_key" && ! model_key_exists "${model_key##*/}"; then
      echo "[boot] model not found locally, skipping: $model_key" >&2
      continue
    fi
    load_key="$(resolve_load_key "$model_key")"
    "$LMS_CLI" unload "$identifier" >/dev/null 2>&1 || true
    ttl_args=()
    [ "${ttl:-0}" != "0" ] && [ -n "${ttl:-}" ] && ttl_args=(--ttl "$ttl")
    # shellcheck disable=SC2086
    "$LMS_CLI" load -c "$context" --gpu "$gpu" --parallel "$parallel" \
      --identifier "$identifier" "${ttl_args[@]}" "$load_key" -y || \
      echo "[boot] failed to load $model_key" >&2
  done < <(
    if [ -x "$MODELS_PY" ]; then "$MODELS_PY" boot-load --catalog "$CATALOG"
    else python3 "$HOME/.local/bin/lmstudio-models" boot-load --catalog "$CATALOG"; fi
  )
fi

for _ in 1 2 3 4 5; do start_lmstudio_server && exit 0; sleep 3; done
echo "lms server start failed after retries" >&2
exit 1
