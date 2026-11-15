"""Exercise BYOR path helpers.

The lookups everything else builds on: the global config dir prefers XDG_CONFIG_HOME and falls back
to ~/.config, and byor's state dir prefers XDG_STATE_HOME and falls back to ~/.local/state.
Repo-root resolution searches with a fixed precedence — an explicit repo argument wins, a .byor
config beats a nearer .git dir, then the nearest git dir, then the start directory itself.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from byor.io.paths import global_config_dir, global_state_dir, resolve_repo_root


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_global_config_dir_prefers_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert global_config_dir() == tmp_path / "xdg" / "byor"


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_global_config_dir_falls_back_to_home_dot_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # Path.home() reads HOME on POSIX and USERPROFILE on Windows; patch the
    # method so the fallback branch is exercised portably.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert global_config_dir() == tmp_path / ".config" / "byor"


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_global_state_dir_prefers_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    assert global_state_dir() == tmp_path / "state" / "byor"


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_global_state_dir_falls_back_to_home_local_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert global_state_dir() == tmp_path / ".local" / "state" / "byor"


def test_explicit_repo_wins_over_search(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert resolve_repo_root(explicit=elsewhere, start=tmp_path) == elsewhere.resolve()


# A nested repo, a submodule, or a worktree placed in its main checkout (`.git` is then a file) is
# its own repo, so its init and its worktree copy act on it rather than on the enclosing byor repo.
@pytest.mark.parametrize("make_git", [Path.mkdir, lambda path: path.write_text("gitdir: elsewhere\n")])
def test_a_nearer_git_dir_is_its_own_root_inside_a_byor_repo(
    tmp_path: Path, make_git: Callable[[Path], object]
) -> None:
    outer = tmp_path / "outer"
    (outer / ".byor").mkdir(parents=True)
    (outer / ".byor" / "config.yml").write_text("version: 1\n")
    inner = outer / "inner"
    inner.mkdir()
    make_git(inner / ".git")
    start = inner / "src"
    start.mkdir()

    assert resolve_repo_root(start=start) == inner.resolve()
    assert resolve_repo_root(start=outer) == outer.resolve()


def test_falls_back_to_nearest_git_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    start = repo / "pkg" / "deep"
    start.mkdir(parents=True)

    assert resolve_repo_root(start=start) == repo.resolve()


def test_falls_back_to_start_directory(tmp_path: Path) -> None:
    assert resolve_repo_root(start=tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize(
    ("variable", "resolve", "default"),
    [
        ("XDG_CONFIG_HOME", global_config_dir, Path(".config")),
        ("XDG_STATE_HOME", global_state_dir, Path(".local") / "state"),
    ],
)
def test_a_relative_xdg_value_is_ignored(
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    variable: str,
    resolve: Callable[[], Path],
    default: Path,
) -> None:
    monkeypatch.setenv(variable, "relative/dir")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert resolve() == tmp_path / default / "byor"
