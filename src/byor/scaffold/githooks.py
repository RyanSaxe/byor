"""Install repository git hook shims for BYOR.

Git shims keep repository mirrors fresh after checkouts and merges, and optionally run the local
gate before commits. This module writes managed hook snippets without taking ownership of unrelated
user hook content.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from byor.errors import ConfigError
from byor.io.fsio import MANAGED_NOTICE, marked_text_status, write_marked_text
from byor.io.gitio import git_output

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "ShimFindings",
    "install_git_shims",
    "install_precommit_shim",
    "shim_findings",
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

# NUL-separated names piped through xargs -0 keep filenames with spaces
# (or any other byte) intact; the plain listing only guards emptiness.
# R is included because rename detection reports a rename-with-edits as
# status R under the new path, which is exactly the file to check.
PRECOMMIT_CONTENT = f"""#!/bin/sh
{SHIM_MARKER}
command -v byor >/dev/null 2>&1 || exit 0
files=$(git diff --cached --name-only --diff-filter=ACMR)
[ -z "$files" ] && exit 0
git diff --cached --name-only --diff-filter=ACMR -z | xargs -0 byor agent-check --files
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


@dataclass
class ShimFindings:
    problems: list[str]
    """Broken byor-managed shims that need a reinstall."""

    notes: list[str]
    """Non-blocking observations, e.g. a user-owned pre-commit hook."""


def shim_findings(repo_root: Path) -> ShimFindings | None:
    """Return read-only findings for installed hook shims, or None when unverifiable.

    Repo config does not record the shim opt-ins, so a repo with no marked hook
    is indistinguishable from one that never installed them: None means there is
    nothing to verify (no .git, core.hooksPath in charge, or no marked shim —
    counting a marker `pre-commit install` displaced to pre-commit.legacy).
    A marked sync shim implies its post-merge/post-checkout partner, so a deleted
    partner is a problem; the standalone pre-commit shim can only be checked for
    drift. Unmarked hooks are user-owned and never compared. A marker displaced
    to pre-commit.legacy is a note while the chained shim is current — init
    refuses to reclaim pre-commit's unmarked hook, so a problem could never
    converge — and a problem once it drifts. When the marked sync shims prove
    the repo opted into byor hooks, an unmarked pre-commit earns an
    informational note.
    """
    if not (repo_root / ".git").exists():
        return None
    if git_output(repo_root, "config", "--get", "core.hooksPath") is not None:
        return None
    hooks_dir = _hooks_dir(repo_root)
    sync_statuses = {
        name: marked_text_status(hooks_dir / name, SHIM_CONTENT, marker=SHIM_MARKER) for name in SHIM_HOOK_NAMES
    }
    precommit_status = marked_text_status(hooks_dir / "pre-commit", PRECOMMIT_CONTENT, marker=SHIM_MARKER)
    legacy_status = marked_text_status(hooks_dir / "pre-commit.legacy", PRECOMMIT_CONTENT, marker=SHIM_MARKER)
    marked = {"unchanged", "drifted"}
    sync_marked = not marked.isdisjoint(sync_statuses.values())
    displaced = precommit_status == "unmarked" and legacy_status in marked
    if not sync_marked and precommit_status not in marked and not displaced:
        return None
    problems: list[str] = []
    notes: list[str] = []
    if sync_marked:
        problems.extend(
            f".git/hooks/{name} is {'missing' if status == 'missing' else 'outdated'}; run `byor init --git-hooks`"
            for name, status in sync_statuses.items()
            if status in ("missing", "drifted")
        )
    if precommit_status == "drifted":
        problems.append(".git/hooks/pre-commit is outdated; run `byor init --private --gate`")
    if displaced and legacy_status == "unchanged":
        notes.append(
            "pre-commit hook was displaced to pre-commit.legacy by `pre-commit install`;"
            " the gate still chains and is current;"
            " run `uvx pre-commit uninstall` to restore byor's shim (removes pre-commit's own hooks)"
        )
    elif displaced:
        problems.append(
            ".git/hooks/pre-commit.legacy is an outdated byor shim that pre-commit still chains;"
            " run `uvx pre-commit uninstall` then `byor init --private --gate`,"
            " or delete .git/hooks/pre-commit.legacy"
        )
    elif precommit_status == "unmarked":
        notes.append("pre-commit hook is user-owned; byor is not managing a commit gate here")
    # Git silently skips a hook without the exec bit, so a chmod -x'd shim is
    # as broken as a deleted one; reinstall restores the bit.
    remedies = dict.fromkeys(SHIM_HOOK_NAMES, "byor init --git-hooks") | {
        "pre-commit": "byor init --private --gate",
        "pre-commit.legacy": "chmod +x .git/hooks/pre-commit.legacy",
    }
    statuses = {**sync_statuses, "pre-commit": precommit_status}
    if displaced and legacy_status == "unchanged":
        # pre-commit runs the chained .legacy hook only when os.access(X_OK)
        # passes and silently skips it otherwise, so the bit is part of health.
        statuses["pre-commit.legacy"] = legacy_status
    problems.extend(
        f".git/hooks/{name} is not executable; run `{remedies[name]}`"
        for name, status in statuses.items()
        if status in marked and not os.access(hooks_dir / name, os.X_OK)
    )
    return ShimFindings(problems=problems, notes=notes)


def _install_shim(hook: Path, content: str, *, line: str) -> list[str]:
    result = write_marked_text(hook, content, marker=SHIM_MARKER)
    if result == "unmarked":
        return [
            f".git/hooks/{hook.name} exists without the BYOR marker; add this line to it:",
            f"  {line}",
        ]
    # Restore the exec bit even on an unchanged shim: a chmod -x'd hook is
    # silently skipped by git, so reinstall must heal the bit, not just bytes.
    hook.chmod(hook.stat().st_mode | 0o111)
    if result == "unchanged":
        return []
    return [f"Installed .git/hooks/{hook.name}"]


def _hooks_dir(repo_root: Path) -> Path:
    # --git-path resolves worktrees to the shared common hooks directory.
    output = git_output(repo_root, "rev-parse", "--git-path", "hooks")
    if output is None:
        msg = f"could not locate the git hooks directory for {repo_root}"
        raise ConfigError(msg)
    return (repo_root / output).resolve()
