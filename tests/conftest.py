"""Shared fixtures: an isolated home and a clean git environment.

Reusable helper functions live in ``support.py``, imported directly via the ``pythonpath =
["tests"]`` setting. This file holds only pytest fixtures, which are auto-discovered across every
test subdirectory.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture
# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
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
# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def clean_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # GIT_* vars leak in when pytest itself runs inside a git hook (pre-commit)
    # and would redirect the nested git calls tests make at tmp repos.
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
