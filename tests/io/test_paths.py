"""Exercise BYOR path helpers.

Two lookups everything else builds on: the global config dir prefers XDG_CONFIG_HOME and falls back
to ~/.config. Repo-root resolution searches with a fixed precedence — an explicit repo argument
wins, a .byor config beats a nearer .git dir, then the nearest git dir, then the start directory
itself.
"""

from pathlib import Path

import pytest

from byor.io.paths import global_config_dir, resolve_repo_root


def test_global_config_dir_prefers_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert global_config_dir() == tmp_path / "xdg" / "byor"


def test_global_config_dir_falls_back_to_home_dot_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # Path.home() reads HOME on POSIX and USERPROFILE on Windows; patch the
    # method so the fallback branch is exercised portably.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert global_config_dir() == tmp_path / ".config" / "byor"


def test_explicit_repo_wins_over_search(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert resolve_repo_root(explicit=elsewhere, start=tmp_path) == elsewhere.resolve()


def test_prefers_byor_config_over_nearer_git_dir(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    (outer / ".byor").mkdir(parents=True)
    (outer / ".byor" / "config.yml").write_text("version: 1\n")
    inner = outer / "inner"
    (inner / ".git").mkdir(parents=True)
    start = inner / "src"
    start.mkdir()

    assert resolve_repo_root(start=start) == outer.resolve()


def test_falls_back_to_nearest_git_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    start = repo / "pkg" / "deep"
    start.mkdir(parents=True)

    assert resolve_repo_root(start=start) == repo.resolve()


def test_falls_back_to_start_directory(tmp_path: Path) -> None:
    assert resolve_repo_root(start=tmp_path) == tmp_path.resolve()
