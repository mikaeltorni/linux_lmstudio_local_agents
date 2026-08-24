#!/usr/bin/env bash
# defaults.sh — Single source of truth for LM Studio local-agent serving.
#
# Sourced by install.sh and lmstudio-boot.sh before writing configs, so changing
# a setting here takes effect everywhere. Model-specific flags (context, gpu,
# parallel) live per-model in model-profiles.json; these are the serving and
# daemon defaults plus fallbacks for the boot script.

# ── LM Studio application ─────────────────────────────────────────────────────
LM_STUDIO_VERSION="0.4.16-2"

# Filesystem layout — single source of truth for where the LM Studio CLI (`lms`)
# is installed. The boot script and any consumer that bakes the path into a
# generated script (e.g. agent_command_center's lmstudio-log-runner) resolve this
# instead of hardcoding "~/.lmstudio/bin". Overridable for non-default installs.
LMSTUDIO_BIN_DIR="${LMSTUDIO_BIN_DIR:-$HOME/.lmstudio/bin}"

# ── Serving / daemon behaviour ────────────────────────────────────────────────
LMSTUDIO_DOWNLOAD_MODELS="1"          # set 0 only for offline/test installation
LMSTUDIO_DAEMON_SETTLE_SECONDS="8"    # wait after lms daemon up before loading
LMSTUDIO_DAEMON_MAX_ATTEMPTS="12"     # daemon-up readiness polls before giving up (cold boot is slow)
LMSTUDIO_ENABLE_LOCAL_SERVICE="1"
LMSTUDIO_CUDA_VISIBLE_DEVICES=""
# Newer llama.cpp materially improves Qwen3 prompt and generation throughput on
# NVIDIA GPUs while retaining the CUDA 12 compatibility floor used by LM Studio.
LMSTUDIO_LLM_RUNTIME_ALIAS="llama.cpp-linux-x86_64-nvidia-cuda12-avx2@2.28.2"
LMSTUDIO_USER_SERVICE_NAME="lmstudio-serve.service"
LMSTUDIO_SERVER_START_EXTRA=""

# ── Fallback boot flags (used when a model profile omits them) ─────────────────
LMSTUDIO_BOOT_CONTEXT="131072"
LMSTUDIO_BOOT_GPU="max"
LMSTUDIO_BOOT_PARALLEL="1"
