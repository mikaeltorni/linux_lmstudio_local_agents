#!/usr/bin/env python3
"""Contract tests for the repository core (ISC_CORE).

The LM Studio app/CLI, the shared runtime assets, and the lmstudio-serve user
service are not optional features. An ISC_COMPONENTS row is always
deselectable ("on" means pre-selected, not mandatory), so expressing the core
as a component let `--select ''` — or a user who deselects every row in the
master menu — produce an install with no LM Studio and no serving at all.
These tests pin the core to the framework's ISC_CORE hook, which runs first and
unconditionally.
"""

import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# Drives the real shared component runtime with every install function stubbed,
# so the test records which entry points a given selection would invoke without
# touching the machine.
PROBE = r"""
set -euo pipefail
SCRIPT_DIR="$1"; MARKER="$2"; shift 2
log() { :; }
source "$ISC_FUNCTIONS_DIR/component_loader.sh"
isc_load_helpers
isc_activate_components
source "$SCRIPT_DIR/installer/components.sh"
install_lmstudio_core() { echo core >> "$MARKER"; }
install_models() { echo models >> "$MARKER"; }
install_lm_studio_app() { echo app >> "$MARKER"; }
configure_serving() { echo serve >> "$MARKER"; }
detect_models() { return 1; }
detect_lm_studio_app() { return 1; }
configure_models() { :; }
report_sudo_required() { :; }
component_main "$@"
"""


def run_probe(tmp_path: Path, *args: str) -> list[str]:
    """Run the public component runtime with isolated installation stubs.

    Parameters: tmp_path - temporary state directory; args - public selection flags.
    Returns: installed stub names in execution order.
    """
    print(f'tmp_path={tmp_path} args={args}')
    marker = tmp_path / 'marker.txt'
    marker.write_text('')
    functions_dir = ROOT_DIR / '.installer-support'
    completed = subprocess.run(
        ['bash', '-c', PROBE, 'probe', str(ROOT_DIR), str(marker), *args],
        cwd=ROOT_DIR, text=True, capture_output=True,
        env={'PATH':'/usr/bin:/bin', 'HOME':str(tmp_path),
             'ISC_FUNCTIONS_DIR':str(functions_dir)},
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result = marker.read_text().split()
    print(result)
    return result



def test_core_installs_even_when_nothing_is_selected(tmp_path):
    """A blank selection must still install the core, not produce a no-op run."""
    assert run_probe(tmp_path, "--select", "") == ["core"]


def test_core_runs_before_any_component(tmp_path):
    """The core must be first so no component layers onto a missing LM Studio."""
    ran = run_probe(tmp_path, "--default")

    assert ran[0] == "core", ran
    assert "app" in ran, ran


def test_serving_is_not_a_deselectable_component():
    """Serving belongs to the core; a 'serve' row would be deselectable again."""
    result = subprocess.run(
        ["bash", str(ROOT_DIR / "install.sh"), "--list-components"],
        cwd=ROOT_DIR, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    ids = [line.split("\t", 1)[0] for line in result.stdout.splitlines()]
    assert "serve" not in ids, result.stdout
    assert "models" in ids


def test_manifest_declares_the_core_hook():
    """ISC_CORE must be wired, and never faked with the preflight hook."""
    manifest = (ROOT_DIR / "installer" / "components.sh").read_text()

    assert 'ISC_CORE="install_lmstudio_core"' in manifest
    # The word may appear in the explanatory comment; only an assignment is a bug.
    assert "ISC_PREFLIGHT=" not in manifest
