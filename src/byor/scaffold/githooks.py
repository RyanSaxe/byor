"""Install repository git hook shims for BYOR.

Git shims keep repository mirrors fresh after checkouts and merges, and optionally run the local
gate before commits. This module writes managed hook snippets without taking ownership of unrelated
user hook content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.errors import ConfigError
from byor.io.fsio import MANAGED_NOTICE, write_marked_text
from byor.io.gitio import git_output

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "install_git_shims",
    "install_precommit_shim",
)

SHIM_HOOK_NAMES = ("post-merge", "post-checkout")

SHIM_MARKER = f"# {MANAGED_NOTICE}"

# `|| true` so the shim can never block a git operation.
SHIM_LINE = "[ -d .byor ] && command -v byor >/dev/null 2>&1 && byor sync || true"

SHIM_CONTENT = f"""#!/bin/sh
{SHIM_MARKER}
{SHIM_LINE}
"""

# The private-mode gate: check staged files on commit, blocking on diagnostics.
# It runs byor directly (byor is the private user's own tool) and no-ops when
# byor is absent, so it never blocks a contributor who has not installed it.
PRECOMMIT_LINE = "byor agent-check --files <staged files>"

PRECOMMIT_CONTENT = f"""#!/bin/sh
{SHIM_MARKER}
command -v byor >/dev/null 2>&1 || exit 0
files=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$files" ] && exit 0
byor agent-check --files $files
"""


def install_git_shims(repo_root: Path) -> list[str]:
    if not (repo_root / ".git").exists():
        msg = f"{repo_root} has no .git directory; cannot install git hook shims"
        raise ConfigError(msg)
    hooks_path = git_output(repo_root, "config", "--get", "core.hooksPath")
    if hooks_path is not None:
        return [
            f"core.hooksPath is set ({hooks_path}); add this line to your post-merge and post-checkout hooks:",
            f"  {SHIM_LINE}",
        ]
    hooks_dir = _hooks_dir(repo_root)
    messages: list[str] = []
    for name in SHIM_HOOK_NAMES:
        messages.extend(_install_shim(hooks_dir / name, SHIM_CONTENT, line=SHIM_LINE))
    return messages


def install_precommit_shim(repo_root: Path) -> list[str]:
    if not (repo_root / ".git").exists():
        msg = f"{repo_root} has no .git directory; cannot install a pre-commit hook"
        raise ConfigError(msg)
    hooks_path = git_output(repo_root, "config", "--get", "core.hooksPath")
    if hooks_path is not None:
        return [
            f"core.hooksPath is set ({hooks_path}); add a pre-commit hook running:",
            f"  {PRECOMMIT_LINE}",
        ]
    hook = _hooks_dir(repo_root) / "pre-commit"
    return _install_shim(hook, PRECOMMIT_CONTENT, line=PRECOMMIT_LINE)


def _install_shim(hook: Path, content: str, *, line: str) -> list[str]:
    result = write_marked_text(hook, content, marker=SHIM_MARKER)
    if result == "unmarked":
        return [
            f".git/hooks/{hook.name} exists without the BYOR marker; add this line to it:",
            f"  {line}",
        ]
    if result == "unchanged":
        return []
    hook.chmod(hook.stat().st_mode | 0o111)
    return [f"Installed .git/hooks/{hook.name}"]


def _hooks_dir(repo_root: Path) -> Path:
    # --git-path resolves worktrees to the shared common hooks directory.
    output = git_output(repo_root, "rev-parse", "--git-path", "hooks")
    if output is None:
        msg = f"could not locate the git hooks directory for {repo_root}"
        raise ConfigError(msg)
    return (repo_root / output).resolve()
