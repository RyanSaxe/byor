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
    "git_output",
    "git_stdout",
)


def git_stdout(repo_root: Path, *args: str) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def git_output(repo_root: Path, *args: str) -> str | None:
    output = (git_stdout(repo_root, *args) or "").strip()
    return output or None
