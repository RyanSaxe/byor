"""The marked git ignore block for BYOLSP's untracked generated state (SPEC 9)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from byolsp.errors import ConfigError
from byolsp.yamlio import write_text_atomic

IgnoreMode = Literal["project", "local"]

IGNORED_PATTERNS = (
    ".byolsp/local.yml",
    ".byolsp/rules/personal/local/*.yml",
    ".byolsp/rules/personal/local/*.yaml",
    ".byolsp/rules/personal/global/*.yml",
    ".byolsp/rules/personal/global/*.yaml",
)

BLOCK_BEGIN = "# >>> Managed by BYOLSP. Manual edits may be overwritten. >>>"
BLOCK_END = "# <<< Managed by BYOLSP <<<"


def ignore_file(repo_root: Path, mode: IgnoreMode) -> Path:
    if mode == "project":
        return repo_root / ".gitignore"
    return repo_root / ".git" / "info" / "exclude"


def write_ignore_block(repo_root: Path, mode: IgnoreMode) -> bool:
    """Write the BYOLSP ignore block, replacing any previous one.

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
