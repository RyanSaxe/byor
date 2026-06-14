"""The marked git ignore block for BYOR's untracked generated state,
plus the `.ignore` files that keep those rules visible to ast-grep.

ast-grep's rule discovery respects gitignore, so the git-ignored personal rule
files would never be loaded inside a git repository. ast-grep also reads
`.ignore` files, which git does not, so a `.ignore` with negation patterns in
each personal rule directory un-ignores the rules for ast-grep alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from byor.errors import ConfigError
from byor.io.fsio import (
    MANAGED_NOTICE,
    MarkedWriteResult,
    write_marked_text,
    write_text_atomic,
)
from byor.io.gitio import git_output

IgnoreMode = Literal["project", "local"]

# `**` so nested rules (e.g. personal/global/python/no-cast.yml) are ignored too.
IGNORED_PATTERNS = (
    ".byor/local.yml",
    ".byor/rules/personal/local/**/*.yml",
    ".byor/rules/personal/local/**/*.yaml",
    ".byor/rules/personal/global/**/*.yml",
    ".byor/rules/personal/global/**/*.yaml",
)

BLOCK_BEGIN = f"# >>> {MANAGED_NOTICE} >>>"
BLOCK_END = "# <<< Managed by BYOR <<<"

VISIBILITY_MARKER = f"# {MANAGED_NOTICE}"

VISIBILITY_PATTERNS = ("!*.yml", "!*.yaml")

VISIBILITY_FILE_CONTENT = (
    f"{VISIBILITY_MARKER}\n"
    "# Git ignores the personal rule files in this directory, and ast-grep's\n"
    "# rule discovery respects gitignore. ast-grep also reads .ignore files\n"
    "# (git does not), so these negations keep the rules visible to ast-grep.\n"
    + "\n".join(VISIBILITY_PATTERNS)
    + "\n"
)


def write_rule_visibility_file(rules_dir: Path) -> MarkedWriteResult:
    """Converge `rules_dir/.ignore` so ast-grep loads the git-ignored rules.

    A user-owned (unmarked) .ignore is never touched; doctor flags it when it
    no longer keeps the rules visible.
    """
    rules_dir.mkdir(parents=True, exist_ok=True)
    return write_marked_text(
        rules_dir / ".ignore", VISIBILITY_FILE_CONTENT, VISIBILITY_MARKER
    )


def rule_visibility_ok(rules_dir: Path) -> bool:
    """Whether `rules_dir/.ignore` un-ignores rule files for ast-grep."""
    path = rules_dir / ".ignore"
    if not path.is_file():
        return False
    lines = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    return all(pattern in lines for pattern in VISIBILITY_PATTERNS)


def ignore_file(repo_root: Path, mode: IgnoreMode) -> Path:
    if mode == "project":
        return repo_root / ".gitignore"
    # --git-path resolves worktrees, whose `.git` is a file, not a directory.
    output = git_output(repo_root, "rev-parse", "--git-path", "info/exclude")
    if output is None:
        raise ConfigError(f"could not locate the git info/exclude file for {repo_root}")
    return (repo_root / output).resolve()


def write_ignore_block(repo_root: Path, mode: IgnoreMode) -> bool:
    """Write the BYOR ignore block, replacing any previous one.

    Idempotent; returns True only when the file changed.
    """
    if mode == "local" and not (repo_root / ".git").exists():
        raise ConfigError(
            f"{repo_root} has no .git directory; cannot use the local ignore mode"
        )
    path = ignore_file(repo_root, mode)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = _with_block(current)
    if updated == current:
        return False
    write_text_atomic(path, updated)
    return True


def _with_block(content: str) -> str:
    block = "\n".join((BLOCK_BEGIN, *IGNORED_PATTERNS, BLOCK_END)) + "\n"
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


def _marker_index(lines: list[str], marker: str, start: int) -> int | None:
    for index in range(start, len(lines)):
        if lines[index].rstrip("\n") == marker:
            return index
    return None
