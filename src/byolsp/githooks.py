"""Git hook shims that close the pull gap by running sync (SPEC 3.3, 15.11)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from byolsp.errors import ConfigError
from byolsp.fsio import write_text_atomic

SHIM_HOOK_NAMES = ("post-merge", "post-checkout")

SHIM_MARKER = "# Managed by BYOLSP. Manual edits may be overwritten."

# `|| true` so the shim can never block a git operation (SPEC 15.11).
SHIM_LINE = "[ -d .byolsp ] && command -v byolsp >/dev/null 2>&1 && byolsp sync || true"

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
    hooks_path = _configured_hooks_path(repo_root)
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
    if hook.is_file():
        content = hook.read_text(encoding="utf-8")
        if SHIM_MARKER not in content:
            return [
                f".git/hooks/{hook.name} exists without the BYOLSP marker; "
                "add this line to it:",
                f"  {SHIM_LINE}",
            ]
        if content == SHIM_CONTENT:
            return []
    write_text_atomic(hook, SHIM_CONTENT)
    hook.chmod(hook.stat().st_mode | 0o111)
    return [f"Installed .git/hooks/{hook.name}"]


def _configured_hooks_path(repo_root: Path) -> str | None:
    return _git_output(repo_root, "config", "--get", "core.hooksPath")


def _hooks_dir(repo_root: Path) -> Path:
    # --git-path resolves worktrees to the shared common hooks directory.
    output = _git_output(repo_root, "rev-parse", "--git-path", "hooks")
    if output is None:
        raise ConfigError(f"could not locate the git hooks directory for {repo_root}")
    return (repo_root / output).resolve()


def _git_output(repo_root: Path, *args: str) -> str | None:
    """Stripped stdout of a git query, or None when git is missing or it fails."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    output = result.stdout.strip()
    return output if result.returncode == 0 and output else None
