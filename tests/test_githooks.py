"""Git hook shims installed by `byolsp init --git-hooks` (SPEC 15.11)."""

import os
from pathlib import Path

import pytest
from conftest import git, make_repo

from byolsp.cli import main
from byolsp.githooks import SHIM_CONTENT, SHIM_LINE, SHIM_MARKER


def git_repo(home: Path) -> Path:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    return repo


def init_with_hooks(repo: Path) -> int:
    return main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"])


def test_init_installs_executable_shims_idempotently(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo(home)

    assert init_with_hooks(repo) == 0

    out = capsys.readouterr().out
    for name in ("post-merge", "post-checkout"):
        hook = repo / ".git" / "hooks" / name
        assert hook.read_text() == SHIM_CONTENT
        assert os.access(hook, os.X_OK)
        assert f"Installed .git/hooks/{name}" in out

    assert init_with_hooks(repo) == 0
    assert "Installed .git/hooks" not in capsys.readouterr().out


def test_unmarked_existing_hook_is_left_untouched(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo(home)
    existing = repo / ".git" / "hooks" / "post-merge"
    existing.write_text("#!/bin/sh\nmy own hook\n")

    assert init_with_hooks(repo) == 0

    assert existing.read_text() == "#!/bin/sh\nmy own hook\n"
    out = capsys.readouterr().out
    assert ".git/hooks/post-merge exists without the BYOLSP marker" in out
    assert SHIM_LINE in out
    assert (repo / ".git" / "hooks" / "post-checkout").read_text() == SHIM_CONTENT


def test_outdated_marked_shim_is_updated(home: Path) -> None:
    repo = git_repo(home)
    stale = repo / ".git" / "hooks" / "post-checkout"
    stale.write_text(f"#!/bin/sh\n{SHIM_MARKER}\nbyolsp sync\n")

    assert init_with_hooks(repo) == 0

    assert stale.read_text() == SHIM_CONTENT


def test_core_hooks_path_repo_gets_the_line_instead_of_files(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo(home)
    git(repo, "config", "core.hooksPath", ".husky")

    assert init_with_hooks(repo) == 0

    out = capsys.readouterr().out
    assert "core.hooksPath is set (.husky)" in out
    assert SHIM_LINE in out
    assert not (repo / ".git" / "hooks" / "post-merge").exists()
    assert not (repo / ".git" / "hooks" / "post-checkout").exists()


def test_shims_install_into_worktrees_common_hooks_dir(home: Path) -> None:
    main_repo = make_repo(home, "main-repo")
    git(main_repo, "init", "--quiet")
    git(main_repo, "commit", "--allow-empty", "-q", "-m", "init")
    worktree = home / "worktree"
    git(main_repo, "worktree", "add", "-q", str(worktree))

    assert init_with_hooks(worktree) == 0

    hook = main_repo / ".git" / "hooks" / "post-merge"
    assert hook.read_text() == SHIM_CONTENT
