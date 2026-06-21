#!/usr/bin/env python3
"""defaults.py — Read LM Studio serving defaults from defaults.sh.

Returns a dict of {varname: value} parsed from the bash defaults file. Scripts
that import this should assign: globals().update(get_defaults()).

  from defaults import get_defaults
  D = get_defaults()
  alias = D["LMSTUDIO_LLM_RUNTIME_ALIAS"]

CONFIG_DIR is resolved relative to this file, so the defaults.sh location is
always correct as long as defaults.py stays beside defaults.sh.
"""

from __future__ import annotations

import re
from pathlib import Path

_DEFAULTS_SH = Path(__file__).with_name("defaults.sh")


def _parse_defaults(path: Path) -> dict:
    """Parse simple ``NAME="value"`` shell assignments into Python values."""
    pat = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"')
    out: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        match = pat.match(line.rstrip())
        if match:
            out[match.group(1)] = match.group(2)
    return out


def get_defaults() -> dict:
    """Return all defaults. Values are strings — callers convert as needed."""
    return _parse_defaults(_DEFAULTS_SH)


class _Namespace(dict):
    """Dict that also exposes its keys as attributes."""

    def __getattr__(self, name: str) -> str:
        """Resolve a default value by attribute name."""
        return self[name]


defaults: _Namespace = _Namespace(get_defaults())
globals().update(defaults)
