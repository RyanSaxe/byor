"""Default-branch detection: origin/HEAD first, then the current branch, then "main".

Generated gate files embed the default branch, so detection must be stable per repo state: origin/HEAD
wins whenever a remote publishes one, the current branch covers fresh local repos without a remote, and
"main" covers directories that are not git repos at all. These tests run real git in throwaway
directories instead of mocking subprocess.
"""

from pathlib import Path

from support import git

from byor.io.gitio import default_branch


def local_repo(tmp_path: Path, *, initial_branch: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", f"--initial-branch={initial_branch}")
    return repo


def test_default_branch_prefers_origin_head_over_the_current_branch(tmp_path: Path) -> None:
    repo = local_repo(tmp_path, initial_branch="feature")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")

    assert default_branch(repo) == "trunk"


def test_default_branch_falls_back_to_the_current_branch_without_a_remote(tmp_path: Path) -> None:
    repo = local_repo(tmp_path, initial_branch="trunk")

    assert default_branch(repo) == "trunk"


def test_default_branch_is_main_outside_a_git_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert default_branch(plain) == "main"
