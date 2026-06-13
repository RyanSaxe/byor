"""Shared scaffolding for CLI-level tests: an isolated home plus rule writers."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from byolsp.cli import main

RULE_TEMPLATE = (
    "id: {rule_id}\n"
    "language: Python\n"
    "message: {message}\n"
    "rule:\n"
    "  pattern: cast($TYPE, $VALUE)\n"
)


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


def git(repo: Path, *argv: str) -> str:
    """Run git in `repo` with an inline throwaway identity; returns stdout."""
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def commit_file(repo: Path, name: str, content: str) -> Path:
    file = repo / name
    file.write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "--quiet", "-m", f"add {name}")
    return file


def make_repo(home: Path, name: str = "repo", *extra: str) -> Path:
    repo = home / name
    repo.mkdir()
    assert main(["init", "--repo", str(repo), "--non-interactive", *extra]) == 0
    return repo


def write_rule(path: Path, rule_id: str, message: str = "Avoid this.") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RULE_TEMPLATE.format(rule_id=rule_id, message=message))
    return path


def write_global_rule(home: Path, relpath: str, rule_id: str) -> Path:
    return write_rule(home / "xdg" / "byolsp" / "rules" / relpath, rule_id)


def mirror(repo: Path) -> Path:
    """The generated copy of global rules that ast-grep reads in this repo."""
    return repo / ".byolsp" / "rules" / "personal" / "global"


def make_editor(directory: Path, content: str) -> str:
    """An $EDITOR value whose command replaces the edited file with `content`.

    Deliberately multi-word so it exercises the shlex.split contract (SPEC 19).
    """
    source = directory / "editor-replacement.yml"
    source.write_text(content)
    copy_into_edited_file = (
        "import shutil, sys; shutil.copyfile(sys.argv[1], sys.argv[2])"
    )
    return shlex.join([sys.executable, "-c", copy_into_edited_file, str(source)])


def substituting_editor(old: str, new: str) -> str:
    """An $EDITOR whose command replaces `old` with `new` in the edited file."""
    substitute = (
        "import pathlib, sys; path = pathlib.Path(sys.argv[1]); "
        f"path.write_text(path.read_text().replace({old!r}, {new!r}))"
    )
    return shlex.join([sys.executable, "-c", substitute])


def noop_editor() -> str:
    """An $EDITOR that exits 0 without touching the file."""
    return shlex.join([sys.executable, "-c", "pass"])


def failing_editor(status: int) -> str:
    """An $EDITOR that exits nonzero without touching the file."""
    return shlex.join([sys.executable, "-c", f"raise SystemExit({status})"])
