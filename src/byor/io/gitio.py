"""Run git commands with clean optional output.

BYOR often needs repository state but should degrade gracefully outside git or before an initial
commit. These helpers keep git invocation consistent and return None for expected command failures.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "default_branch",
    "git_output",
    "git_stdout",
)


def default_branch(repo_root: Path) -> str:
    """Name of the repo's default branch, for enforcement that targets it.

    Prefers origin/HEAD because it is stable across checkouts, so generated
    files that embed the branch never flap under self-heal; without a remote
    it falls back to the current branch, and outside git to "main".
    """
    origin_head = git_output(repo_root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if origin_head is not None:
        return origin_head.removeprefix("origin/")
    return git_output(repo_root, "branch", "--show-current") or "main"


def git_stdout(repo_root: Path, *args: str) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        # git emits raw UTF-8; decoding with the locale code page mojibakes or
        # crashes on Windows. "replace" because this output is parsed/displayed,
        # never round-tripped byte-for-byte.
        result = subprocess.run(
            [git, "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def git_output(repo_root: Path, *args: str) -> str | None:
    output = (git_stdout(repo_root, *args) or "").strip()
    return output or None
