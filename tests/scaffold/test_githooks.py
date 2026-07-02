"""Git hook shims installed by `byor init --git-hooks`.

The shims are marked files, so installs are idempotent, outdated shims are rewritten, and an
unmarked user hook is never touched. Layout quirks get their own cases: a repo with core.hooksPath
set gets the shim line printed for its own hook manager instead of files, and worktrees install into
the shared common hooks dir.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support import commit_file, git, make_repo, write_global_rule

from byor.cli import main
from byor.scaffold.githooks import SHIM_CONTENT, SHIM_LINE, SHIM_MARKER, shim_problems


def git_repo(home: Path) -> Path:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    return repo


def test_init_installs_executable_shims_idempotently(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = git_repo(home)

    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0

    out = capsys.readouterr().out
    for name in ("post-merge", "post-checkout"):
        hook = repo / ".git" / "hooks" / name
        assert hook.read_text() == SHIM_CONTENT
        assert os.access(hook, os.X_OK)
        assert f"Installed .git/hooks/{name}" in out

    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0
    assert "Installed .git/hooks" not in capsys.readouterr().out


def test_unmarked_existing_hook_is_left_untouched(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = git_repo(home)
    existing = repo / ".git" / "hooks" / "post-merge"
    existing.write_text("#!/bin/sh\nmy own hook\n")

    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0

    assert existing.read_text() == "#!/bin/sh\nmy own hook\n"
    out = capsys.readouterr().out
    assert ".git/hooks/post-merge exists without the BYOR marker" in out
    assert SHIM_LINE in out
    assert (repo / ".git" / "hooks" / "post-checkout").read_text() == SHIM_CONTENT


def test_outdated_marked_shim_is_updated(home: Path) -> None:
    repo = git_repo(home)
    stale = repo / ".git" / "hooks" / "post-checkout"
    stale.write_text(f"#!/bin/sh\n{SHIM_MARKER}\nbyor sync\n")

    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0

    assert stale.read_text() == SHIM_CONTENT


def test_chmod_minus_x_shim_is_reported_and_reinstall_restores_the_bit(home: Path) -> None:
    """Git silently skips a non-executable hook, so the bit is part of health.

    The writer used to chmod only on content changes, so reinstall could not
    heal a stripped bit, and shim_problems never looked at it: a chmod -x'd
    shim reported healthy while git ignored it.
    """
    repo = git_repo(home)
    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0
    hook = repo / ".git" / "hooks" / "post-merge"
    hook.chmod(0o644)
    if os.access(hook, os.X_OK):
        pytest.skip("cannot clear the exec bit on this platform")

    assert ".git/hooks/post-merge is not executable; run `byor init --git-hooks`" in (shim_problems(repo) or [])

    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0
    assert os.access(hook, os.X_OK)
    assert not shim_problems(repo)


def test_core_hooks_path_repo_gets_the_line_instead_of_files(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = git_repo(home)
    git(repo, "config", "core.hooksPath", ".husky")

    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0

    out = capsys.readouterr().out
    assert "core.hooksPath is set (.husky)" in out
    assert SHIM_LINE in out
    assert not (repo / ".git" / "hooks" / "post-merge").exists()
    assert not (repo / ".git" / "hooks" / "post-checkout").exists()


def test_precommit_shim_blocks_a_violating_file_whose_name_has_a_space(home: Path) -> None:
    """The shim used to word-split staged filenames, scanning nothing.

    `my file.py` became two bogus paths that agent-check silently dropped, so
    a rule-violating commit passed unscanned. The hook runs for real here:
    the staged file violates a synced global rule, so it must exit nonzero.
    """
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--private", "--gate"]) == 0
    (repo / "my file.py").write_text("from typing import cast\n\nx = cast(int, 1)\n")
    git(repo, "add", "my file.py")

    # The subprocess resolves ~ from the environment, so point it at the sandbox.
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    hook = repo / ".git" / "hooks" / "pre-commit"
    # Git runs hooks through sh, and Windows cannot exec a shell script
    # directly — invoke the shim the way git does (Git Bash ships sh there).
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("running the hook requires an `sh` on PATH")
    result = subprocess.run([sh, str(hook)], cwd=repo, env=env, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "no-cast" in result.stdout + result.stderr


def test_precommit_shim_blocks_a_renamed_and_modified_violating_file(home: Path) -> None:
    """The shim's --diff-filter used to omit R, skipping staged renames.

    Git's default rename detection turns a rename-with-edits into status R
    under the new path, so the old ACM filter checked nothing and a violating
    commit passed unscanned.
    """
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    clean_lines = "".join(f"keep_{index} = {index}\n" for index in range(10))
    commit_file(repo, "keep.py", content=clean_lines)
    assert main(["init", "--repo", str(repo), "--non-interactive", "--private", "--gate"]) == 0
    git(repo, "mv", "keep.py", "moved.py")
    (repo / "moved.py").write_text(clean_lines + "from typing import cast\n\nx = cast(int, 1)\n")
    git(repo, "add", "moved.py")
    assert git(repo, "diff", "--cached", "--name-status").startswith("R")

    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    hook = repo / ".git" / "hooks" / "pre-commit"
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("running the hook requires an `sh` on PATH")
    result = subprocess.run([sh, str(hook)], cwd=repo, env=env, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "no-cast" in result.stdout + result.stderr


def test_shims_install_into_worktrees_common_hooks_dir(home: Path) -> None:
    main_repo = make_repo(home, name="main-repo")
    git(main_repo, "init", "--quiet")
    git(main_repo, "commit", "--allow-empty", "-q", "-m", "init")
    worktree = home / "worktree"
    git(main_repo, "worktree", "add", "-q", str(worktree))

    assert main(["init", "--repo", str(worktree), "--non-interactive", "--git-hooks"]) == 0

    hook = main_repo / ".git" / "hooks" / "post-merge"
    assert hook.read_text() == SHIM_CONTENT
