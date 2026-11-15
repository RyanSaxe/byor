"""Manage BYOR ignore files and rule visibility.

Private and public modes need different ignore targets while ast-grep still needs mirrored personal
rules to remain discoverable. This module owns the generated ignore blocks and visibility files that
balance those constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

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
    "IgnoreBlockStatus",
    "ignore_block_statuses",
    "ignore_file",
    "outdated_ignore_blocks",
    "refresh_ignore_blocks",
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


BlockState = Literal["current", "outdated", "damaged", "unreadable"]


@dataclass(frozen=True)
class IgnoreBlockStatus:
    """The state of the byor block in one ignore file.

    `current` and `outdated` describe a well-formed block whose patterns do or do not match byor's
    current ones. `damaged` means the markers do not pair up as one begin and one end, and
    `unreadable` means the file is not UTF-8. A file with no marker at all gets no status.
    """

    path: Path
    state: BlockState


@dataclass(frozen=True)
class _Block:
    begin: int
    end: int
    patterns: tuple[str, ...]
    current_patterns: tuple[str, ...]


def ignore_block_statuses(repo_root: Path) -> list[IgnoreBlockStatus]:
    """Classify the byor block in `.gitignore` and in `.git/info/exclude`.

    A block counts only when its file has exactly one begin marker and one end marker after it.
    Any other arrangement is `damaged`, because a rewrite could not tell which lines are byor's.
    A block listing `.byor/` is private and any other is shared, so a shared block in
    `.git/info/exclude` stays shared. A file that is not UTF-8 is `unreadable` rather than an
    error, since git reads it fine.
    """
    statuses: list[IgnoreBlockStatus] = []
    for path in _ignore_files(repo_root):
        lines = _read_lines(path)
        block = "unreadable" if lines is None else _find_block(lines)
        if block is None:
            continue
        if isinstance(block, _Block):
            state: BlockState = "current" if block.patterns == block.current_patterns else "outdated"
            statuses.append(IgnoreBlockStatus(path, state))
        else:
            statuses.append(IgnoreBlockStatus(path, block))
    return statuses


def outdated_ignore_blocks(repo_root: Path) -> list[Path]:
    """Return each ignore file whose well-formed byor block predates byor's current patterns.

    byor adds a pattern whenever it gains a personal path, as it did for command-rule copies, and
    a block written before that leaves the new files committable. Damaged and unreadable blocks
    are not listed, because `refresh_ignore_blocks` never rewrites them.
    """
    return [status.path for status in ignore_block_statuses(repo_root) if status.state == "outdated"]


def refresh_ignore_blocks(repo_root: Path) -> list[Path]:
    """Rewrite the pattern lines of every outdated byor block; return the files changed.

    Self-heal and `byor sync` call this so a repo initialized by an older byor picks up new
    patterns without a second `byor init`. Only the lines between the two markers change: the
    markers, every user line and the file's line endings stay byte-for-byte, and a damaged,
    missing or unreadable block is left alone. A block keeps its own setup, shared or private, so
    this never turns a shared repo private or the reverse. Raises OSError when a file cannot be
    replaced.
    """
    changed: list[Path] = []
    for path in _ignore_files(repo_root):
        lines = _read_lines(path)
        block = None if lines is None else _find_block(lines)
        if lines is None or not isinstance(block, _Block) or block.patterns == block.current_patterns:
            continue
        newline = "\r\n" if lines[block.begin].endswith("\r\n") else "\n"
        patterns = [f"{pattern}{newline}" for pattern in block.current_patterns]
        write_text_atomic(path, "".join([*lines[: block.begin + 1], *patterns, *lines[block.end :]]))
        changed.append(path)
    return changed


def _ignore_files(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for private in (False, True):
        try:
            path = ignore_file(repo_root, private=private)
        except ConfigError:
            continue
        if path.is_file():
            paths.append(path)
    return paths


def _read_lines(path: Path) -> list[str] | None:
    # Decoding the bytes keeps CRLF intact, which read_text's newline translation would not.
    try:
        return path.read_bytes().decode("utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None


def _find_block(lines: list[str]) -> _Block | Literal["damaged"] | None:
    begins = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == BLOCK_BEGIN]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == BLOCK_END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        return "damaged"
    begin, end = begins[0], ends[0]
    patterns = tuple(line.rstrip("\r\n") for line in lines[begin + 1 : end])
    # A block's own patterns say which setup wrote it, wherever the block sits: byor 0.2.0's
    # `--ignore-mode local` put a shared block in .git/info/exclude.
    return _Block(begin, end, patterns, current_patterns=_patterns(private=".byor/" in patterns))


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
    path = ignore_file(repo_root, private=private)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = _with_block(current, _patterns(private=private))
    if updated == current:
        return False
    write_text_atomic(path, updated)
    return True


def _patterns(*, private: bool) -> tuple[str, ...]:
    return PRIVATE_IGNORED_PATTERNS if private else IGNORED_PATTERNS


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
