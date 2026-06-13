"""Shared fixtures: an isolated home and a clean git environment.

Reusable helper functions live in ``support.py`` (imported directly via the
``pythonpath = ["tests"]`` setting); this file holds only pytest fixtures, which
are auto-discovered across every test subdirectory.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandbox holding repos and the global config dir (via XDG_CONFIG_HOME)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


@pytest.fixture(autouse=True)
def clean_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """GIT_* vars leak in when pytest itself runs inside a git hook (pre-commit)
    and would redirect the nested git calls tests make at tmp repos."""
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
