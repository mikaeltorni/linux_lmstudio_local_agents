#!/usr/bin/env python3
"""Contract tests for the LM Studio SDK advanced-load helper."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
LOADER = ROOT / "scripts" / "lmstudio_sdk_loader.mjs"
CATALOG = ROOT / "configs" / "lmstudio" / "model-profiles.json"
SELECTOR = ROOT / "scripts" / "lmstudio_model_selector.py"


def _selector():
    """Load the shipped model selector used to pack catalog flags."""
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("lmstudio_model_selector", SELECTOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dry_run_maps_4090_tps_load_config():
    """The helper maps every benchmarked 4090 flag to the official SDK config."""
    result = subprocess.run(
        [
            "node", str(LOADER), "--dry-run",
            "--model", "qwen3.8-27b@q3_k_xl",
            "--identifier", "qwen3.8-27b",
            "--context-length", "131072",
            "--gpu", "max",
            "--parallel", "1",
            "--k-cache", "q8_0",
            "--v-cache", "q8_0",
            "--context-checkpoints", "4",
            "--eval-batch-size", "2048",
            "--physical-batch-size", "512",
            "--mtp",
            "--flash-attention",
            "--offload-kv-cache",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["model"] == "qwen3.8-27b@q3_k_xl"
    assert payload["identifier"] == "qwen3.8-27b"
    config = payload["config"]
    assert config["contextLength"] == 131072
    assert config["maxParallelPredictions"] == 1
    assert config["gpu"] == {"ratio": "max"}
    assert config["llamaKCacheQuantizationType"] == "q8_0"
    assert config["llamaVCacheQuantizationType"] == "q8_0"
    assert config["contextCheckpoints"] == 4
    assert config["evalBatchSize"] == 2048
    assert config["physicalBatchSize"] == 512
    assert config["speculativeDraftMtp"] is True
    assert config["flashAttention"] is True
    assert config["offloadKVCacheToGpu"] is True


def test_shipped_qwen_profile_packs_gpu_kv_winner():
    """The catalog profile packs through sdk_loader_args into the SDK dry-run."""
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    qwen = next(profile for profile in catalog["profiles"] if profile["id"] == "qwen3.8-27b")
    assert qwen["model_key"] == "qwen3.8-27b"
    assert qwen["load_key"] == "qwen3.8-27b@q3_k_xl"
    assert qwen["model_path"].endswith("Qwen3.8-27B-UD-Q3_K_XL.gguf")
    assert qwen["sha256"] == "8c2a45ff85e7674ca185ec8eb6cdeab0e617ed9d8018caed0b64380eb2a67a5e"
    packed = _selector().sdk_loader_args(qwen, qwen["load_key"])
    result = subprocess.run(
        ["node", str(LOADER), "--dry-run", *packed[2:]],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["model"] == "qwen3.8-27b@q3_k_xl"
    assert payload["identifier"] == "qwen3.8-27b"
    config = payload["config"]
    assert config["contextLength"] == qwen["context_length"] == 131072
    assert config["gpu"] == {"ratio": "max"}
    assert config["llamaKCacheQuantizationType"] == "q8_0"
    assert config["llamaVCacheQuantizationType"] == "q8_0"
    assert config["evalBatchSize"] == 2048
    assert config["flashAttention"] is True
    assert config["offloadKVCacheToGpu"] is True
    assert config["speculativeDraftMtp"] is True


def test_loader_searches_nested_standard_plugin_directories():
    """SDK discovery handles LM Studio's plugins/lmstudio/<plugin> layout."""
    source = LOADER.read_text(encoding="utf-8")
    assert "const visit = (directory, depth)" in source
    assert "visit(join(directory, entry.name), depth + 1)" in source
