"""Shared fixtures: an isolated home and a clean git environment.

Reusable helper functions live in ``support.py``, imported directly via the ``pythonpath =
["tests"]`` setting. This file holds only pytest fixtures, which are auto-discovered across every
test subdirectory.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Create a sandbox for repos, global config, and the home directory.

    XDG_CONFIG_HOME points the global config at the sandbox, and Path.home() is
    redirected there too so global state byor keeps under `~` — `~/sgconfig.yml`,
    the skill/hook/plugin dirs — never touches (or reads) the real home.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def clean_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
