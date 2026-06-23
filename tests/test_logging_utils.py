#!/usr/bin/env python3
"""Tests for the centralized Python logging helpers."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
SPEC = importlib.util.spec_from_file_location("lmla_logging_utils", SCRIPTS_DIR / "lmla_logging_utils.py")
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)


def test_get_logger_is_idempotent_and_writes_under_repo_log():
    """The centralized logger installs one repository .log file handler."""
    logger = MOD.get_logger("lmstudio_test_logger", "lmstudio_test_logger.log")
    again = MOD.get_logger("lmstudio_test_logger", "lmstudio_test_logger.log")

    assert logger is again
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, logging.FileHandler)
    assert Path(handler.baseFilename).parent == ROOT_DIR / ".log"


def test_log_call_records_arguments_and_return(caplog):
    """The tracing decorator logs bound arguments on entry and return on exit."""
    logger = logging.getLogger("lmstudio_trace_test")
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.DEBUG)

    @MOD.log_call(logger)
    def sample(first: str, second: int = 2) -> str:
        """Return a value that should appear in the trace."""
        return f"{first}:{second}"

    with caplog.at_level(logging.DEBUG, logger="lmstudio_trace_test"):
        assert sample("model", second=7) == "model:7"

    messages = [record.getMessage() for record in caplog.records]
    assert any("ENTER test_logging_utils.py:test_log_call_records_arguments_and_return.<locals>.sample" in message for message in messages)
    assert any("first='model', second=7" in message for message in messages)
    assert any("EXIT test_logging_utils.py:test_log_call_records_arguments_and_return.<locals>.sample -> 'model:7'" in message for message in messages)
