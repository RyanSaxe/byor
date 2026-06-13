"""The one git subprocess query shared by modules that read repository state."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_stdout(repo_root: Path, *args: str) -> str | None:
    """Raw stdout of a git query, or None when git is missing or it fails."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None
