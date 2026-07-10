"""Manage BYOR ignore files and rule visibility.

Private and public modes need different ignore targets while ast-grep still needs mirrored personal
rules to remain discoverable. This module owns the generated ignore blocks and visibility files that
balance those constraints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.errors import ConfigError
from byor.io.fsio import (
    MANAGED_NOTICE,
    MarkedWriteResult,
    write_marked_text,
    write_text_atomic,
)
from byor.io.gitio import git_output

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "ignore_block_current",
    "ignore_file",
    "rule_visibility_ok",
    "write_ignore_block",
    "write_rule_visibility_file",
)

# The always-ignored personal state, written to .gitignore in a shared setup.
# `**` so nested rules (e.g. personal/global/python/no-cast.yml) are ignored too.
IGNORED_PATTERNS = (
    ".byor/local.yml",
    ".byor/rules/personal/local/**/*.yml",
    ".byor/rules/personal/local/**/*.yaml",
    ".byor/rules/personal/global/**/*.yml",
    ".byor/rules/personal/global/**/*.yaml",
    ".byor/rules/personal/packages/**/*.yml",
    ".byor/rules/personal/packages/**/*.yaml",
    ".byor/commands/personal/local/**/*.yml",
    ".byor/commands/personal/local/**/*.yaml",
    ".byor/commands/personal/global/**/*.yml",
    ".byor/commands/personal/global/**/*.yaml",
    ".byor/commands/personal/packages/**/*.yml",
    ".byor/commands/personal/packages/**/*.yaml",
)

# Private setup: hide byor's entire footprint via .git/info/exclude, so a repo
# can carry byor for one contributor without committing anything.
PRIVATE_IGNORED_PATTERNS = (
    ".byor/",
    "sgconfig.yml",
)

BLOCK_BEGIN = f"# >>> {MANAGED_NOTICE} >>>"
BLOCK_END = "# <<< Managed by BYOR <<<"

VISIBILITY_MARKER = f"# {MANAGED_NOTICE}"

VISIBILITY_PATTERNS = ("!*.yml", "!*.yaml")

VISIBILITY_FILE_CONTENT = (
    f"{VISIBILITY_MARKER}\n"
    "# Git ignores the personal rule files in this directory, and ast-grep's\n"
    "# rule discovery respects gitignore. ast-grep also reads .ignore files\n"
    "# (git does not), so these negations keep the rules visible to ast-grep.\n" + "\n".join(VISIBILITY_PATTERNS) + "\n"
)


def write_rule_visibility_file(rules_dir: Path, *, force: bool = False) -> MarkedWriteResult:
    """Write the `.ignore` negations that keep git-ignored rules visible to ast-grep.

    An unmarked `.ignore` is normally user-owned and left alone. `force` — used
    for the wholly byor-owned mirror directories — reclaims even an unmarked
    file, but only when it fails `rule_visibility_ok`: a user file that already
    keeps the rules visible is doing its job and stays.
    """
    rules_dir.mkdir(parents=True, exist_ok=True)
    result = write_marked_text(rules_dir / ".ignore", VISIBILITY_FILE_CONTENT, marker=VISIBILITY_MARKER)
    if result == "unmarked" and force and not rule_visibility_ok(rules_dir):
        write_text_atomic(rules_dir / ".ignore", VISIBILITY_FILE_CONTENT)
        return "written"
    return result


def rule_visibility_ok(rules_dir: Path) -> bool:
    path = rules_dir / ".ignore"
    if not path.is_file():
        return False
    lines = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    return all(pattern in lines for pattern in VISIBILITY_PATTERNS)


def ignore_block_current(repo_root: Path) -> bool:
    """Return True when a current byor ignore block keeps personal state uncommittable.

    A shared setup carries the block in .gitignore; a private setup hides the
    whole footprint via .git/info/exclude. Either current block keeps personal
    rules and .byor/local.yml out of commits, so both targets are accepted.
    The shared target is tried first: it needs no git subprocess.
    """
    for private in (False, True):
        try:
            path = ignore_file(repo_root, private=private)
        except ConfigError:
            continue
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        patterns = PRIVATE_IGNORED_PATTERNS if private else IGNORED_PATTERNS
        if _with_block(content, patterns) == content:
            return True
    return False


def ignore_file(repo_root: Path, *, private: bool) -> Path:
    if not private:
        return repo_root / ".gitignore"
    # --git-path resolves worktrees, whose `.git` is a file, not a directory.
    output = git_output(repo_root, "rev-parse", "--git-path", "info/exclude")
    if output is None:
        msg = f"could not locate the git info/exclude file for {repo_root}"
        raise ConfigError(msg)
    return (repo_root / output).resolve()


def write_ignore_block(repo_root: Path, *, private: bool) -> bool:
    """Write the BYOR ignore block, replacing any previous one.

    A shared setup ignores only the always-personal state, in .gitignore. A
    private setup hides byor's whole footprint via .git/info/exclude so nothing
    byor creates is tracked. Idempotent; returns True only when the file changed.
    """
    if private and not (repo_root / ".git").exists():
        msg = f"{repo_root} has no .git directory; cannot use private mode"
        raise ConfigError(msg)
    patterns = PRIVATE_IGNORED_PATTERNS if private else IGNORED_PATTERNS
    path = ignore_file(repo_root, private=private)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = _with_block(current, patterns)
    if updated == current:
        return False
    write_text_atomic(path, updated)
    return True


def _with_block(content: str, patterns: tuple[str, ...]) -> str:
    block = "\n".join((BLOCK_BEGIN, *patterns, BLOCK_END)) + "\n"
    lines = content.splitlines(keepends=True)
    begin = _marker_index(lines, BLOCK_BEGIN, start=0)
    if begin is None:
        if content and not content.endswith("\n"):
            content += "\n"
        separator = "\n" if content else ""
        return content + separator + block
    end = _marker_index(lines, BLOCK_END, start=begin + 1)
    # A missing end marker means the block was damaged; reclaim through EOF.
    tail = "".join(lines[end + 1 :]) if end is not None else ""
    return "".join(lines[:begin]) + block + tail


def _marker_index(lines: list[str], marker: str, *, start: int) -> int | None:
    for index in range(start, len(lines)):
        if lines[index].rstrip("\n") == marker:
            return index
    return None
