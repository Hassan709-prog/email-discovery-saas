"""Repository-wide pytest configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest temporary files isolated inside the writable project tree."""
    if config.option.basetemp is None:
        project_root = Path(__file__).resolve().parent
        temp_root = project_root / ".pytest-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(temp_root / f"run-{os.getpid()}")
