"""Git hook shims that close the pull gap by running sync."""

from __future__ import annotations

from pathlib import Path

from byor.errors import ConfigError
from byor.io.fsio import MANAGED_NOTICE, write_marked_text
from byor.io.gitio import git_output

SHIM_HOOK_NAMES = ("post-merge", "post-checkout")

SHIM_MARKER = f"# {MANAGED_NOTICE}"

# `|| true` so the shim can never block a git operation.
SHIM_LINE = "[ -d .byor ] && command -v byor >/dev/null 2>&1 && byor sync || true"

SHIM_CONTENT = f"""#!/bin/sh
{SHIM_MARKER}
{SHIM_LINE}
"""


def install_git_shims(repo_root: Path) -> list[str]:
    """Install marked post-merge/post-checkout shims; returns summary lines.

    Unmarked existing hooks and repos with core.hooksPath set are never
    touched: the user gets the one line to add to their own hook setup.
    """
    if not (repo_root / ".git").exists():
        raise ConfigError(
            f"{repo_root} has no .git directory; cannot install git hook shims"
        )
    hooks_path = git_output(repo_root, "config", "--get", "core.hooksPath")
    if hooks_path is not None:
        return [
            f"core.hooksPath is set ({hooks_path}); add this line to your "
            "post-merge and post-checkout hooks:",
            f"  {SHIM_LINE}",
        ]
    hooks_dir = _hooks_dir(repo_root)
    messages: list[str] = []
    for name in SHIM_HOOK_NAMES:
        messages.extend(_install_shim(hooks_dir / name))
    return messages


def _install_shim(hook: Path) -> list[str]:
    result = write_marked_text(hook, SHIM_CONTENT, SHIM_MARKER)
    if result == "unmarked":
        return [
            f".git/hooks/{hook.name} exists without the BYOR marker; "
            "add this line to it:",
            f"  {SHIM_LINE}",
        ]
    if result == "unchanged":
        return []
    hook.chmod(hook.stat().st_mode | 0o111)
    return [f"Installed .git/hooks/{hook.name}"]


def _hooks_dir(repo_root: Path) -> Path:
    # --git-path resolves worktrees to the shared common hooks directory.
    output = git_output(repo_root, "rev-parse", "--git-path", "hooks")
    if output is None:
        raise ConfigError(f"could not locate the git hooks directory for {repo_root}")
    return (repo_root / output).resolve()
