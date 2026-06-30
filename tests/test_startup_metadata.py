#!/usr/bin/env python3
"""Contract tests for LM Studio's master-menu startup metadata."""

import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def run_install(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the standalone wrapper with a read-only contract argument."""
    return subprocess.run(
        ["bash", str(ROOT_DIR / "install.sh"), *args],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        check=False,
    )


def test_lm_studio_gui_is_startup_capable_but_default_off():
    """LM Studio should expose [S] without launching its GUI by default."""
    result = run_install("--list-startup-components")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "lm_studio_app\tlm-studio.desktop|lmstudio.desktop\toff"
    )


def test_lm_studio_app_is_grouped_as_an_application():
    """The GUI component should render under an Applications section."""
    result = run_install("--list-components")

    assert result.returncode == 0, result.stderr
    rows = {line.split("\t", 1)[0]: line for line in result.stdout.splitlines()}
    assert rows["lm_studio_app"].endswith("\tApplications")
