#!/usr/bin/env python3
"""Contract tests for the LM Studio SDK advanced-load helper."""

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent.parent
LOADER = ROOT / "scripts" / "lmstudio_sdk_loader.mjs"


def test_dry_run_maps_optimized_128k_load_config():
    """The helper maps every benchmarked flag to the official SDK config."""
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
    assert config["llamaKCacheQuantizationType"] == "q8_0"
    assert config["llamaVCacheQuantizationType"] == "q8_0"
    assert config["contextCheckpoints"] == 4
    assert config["speculativeDraftMtp"] is True


def test_loader_searches_nested_standard_plugin_directories():
    """SDK discovery handles LM Studio's plugins/lmstudio/<plugin> layout."""
    source = LOADER.read_text(encoding="utf-8")
    assert "const visit = (directory, depth)" in source
    assert "visit(join(directory, entry.name), depth + 1)" in source
