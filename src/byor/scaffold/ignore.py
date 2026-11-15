"""Manage BYOR ignore files and rule visibility.

Private and public modes need different ignore targets while ast-grep still needs mirrored personal
rules to remain discoverable. This module owns the generated ignore blocks and visibility files that
balance those constraints.
"""

from __future__ import annotations

import os
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
    "RefreshResult",
    "RemovedPrivateBlock",
    "has_private_block",
    "ignore_block_statuses",
    "ignore_file",
    "outdated_ignore_blocks",
    "refresh_ignore_blocks",
    "remove_private_block",
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
BYTE_ORDER_MARK = "\ufeff"

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


BlockState = Literal["current", "outdated", "edited", "damaged", "unreadable"]


@dataclass(frozen=True)
class IgnoreBlockStatus:
    """The state of the byor block in one ignore file.

    `current` and `outdated` describe a well-formed block that does or does not list every pattern
    its setup needs; extra lines a user added inside the markers do not matter. `edited` means the
    block's lines no longer say whether it belongs to a shared or a private setup, so byor cannot
    safely add to it. `damaged` means the markers do not pair up as one begin and one end, and
    `unreadable` means byor could not read the file, for the `reason` given. A file with no marker
    at all gets no status. `writable` says whether byor could rewrite the file.
    """

    path: Path
    state: BlockState
    writable: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class _Block:
    begin: int
    end: int
    missing: tuple[str, ...] | None
    """byor's patterns the block lacks for its setup, or None when its setup cannot be told."""


def ignore_block_statuses(repo_root: Path) -> list[IgnoreBlockStatus]:
    """Classify the byor block in `.gitignore` and in `.git/info/exclude`.

    A block counts only when its file has exactly one begin marker and one end marker after it.
    Any other arrangement is `damaged`, because a rewrite could not tell which lines are byor's.
    The block's own lines say which setup wrote it, wherever it sits, so a shared block that byor
    0.2.0 put in `.git/info/exclude` stays shared, and a private block keeps hiding all of `.byor/`.
    """
    statuses: list[IgnoreBlockStatus] = []
    for path in _ignore_files(repo_root):
        read = _read_lines(path)
        if isinstance(read, _Unreadable):
            if read.has_marker:
                statuses.append(IgnoreBlockStatus(path, "unreadable", reason=read.reason))
            continue
        block = _find_block(read)
        if block is None:
            continue
        if block == "damaged":
            statuses.append(IgnoreBlockStatus(path, "damaged"))
            continue
        state: BlockState = "edited" if block.missing is None else "outdated" if block.missing else "current"
        statuses.append(IgnoreBlockStatus(path, state, writable=state != "outdated" or _writable(path)))
    return statuses


def outdated_ignore_blocks(repo_root: Path) -> list[IgnoreBlockStatus]:
    """Return the status of each well-formed byor block that lacks some of byor's current patterns.

    byor adds a pattern whenever it gains a personal path, as it did for command-rule copies, and
    a block written before that leaves the new files committable. Check `writable` before telling
    the user that `byor sync` fixes one.
    """
    return [status for status in ignore_block_statuses(repo_root) if status.state == "outdated"]


@dataclass(frozen=True)
class RefreshResult:
    changed: list[Path]
    unwritable: list[tuple[Path, str]]
    """Outdated blocks byor could not rewrite, with the reason, for the caller to report."""


def refresh_ignore_blocks(repo_root: Path) -> RefreshResult:
    """Add the patterns each outdated byor block lacks, just before its end marker.

    Self-heal and `byor sync` call this so a repo initialized by an older byor picks up new
    patterns without a second `byor init`. Every line already in the file stays byte-for-byte,
    including lines a user added inside the markers, and the added lines use the block's own line
    endings. A damaged, edited, missing or unreadable block is left alone, and a block keeps its own
    setup, so this never turns a shared repo private or the reverse. A symlinked ignore file is
    written through to its target. A file byor cannot write is reported in `unwritable` and never
    replaced. While git runs byor, as the post-checkout shim does, a tracked `.gitignore` is left
    alone: changing it there leaves the work tree dirty, which blocks the next checkout or rebase,
    and `git checkout -- .gitignore` would fire the shim and dirty it again. The next byor command
    the user runs updates it.
    """
    changed: list[Path] = []
    unwritable: list[tuple[Path, str]] = []
    under_git = _run_by_git()
    for path in _ignore_files(repo_root):
        lines = _read_lines(path)
        block = None if isinstance(lines, _Unreadable) else _find_block(lines)
        if isinstance(lines, _Unreadable) or not isinstance(block, _Block) or not block.missing:
            continue
        if under_git and _tracked(repo_root, path):
            continue
        if not _writable(path):
            unwritable.append((path, "the file is not writable"))
            continue
        newline = "\r\n" if lines[block.begin].endswith("\r\n") else "\n"
        added = [f"{pattern}{newline}" for pattern in block.missing]
        try:
            write_text_atomic(path.resolve(), "".join([*lines[: block.end], *added, *lines[block.end :]]))
        except OSError as error:
            unwritable.append((path, error.strerror or str(error)))
            continue
        changed.append(path)
    return RefreshResult(changed=changed, unwritable=unwritable)


# git exports GIT_PREFIX to every process it starts after finding the repository, such as a hook,
# an alias or `git rebase -x`, and a shell the user opened does not have it.
def _run_by_git() -> bool:
    return "GIT_PREFIX" in os.environ


def _tracked(repo_root: Path, path: Path) -> bool:
    relpath = os.path.relpath(path, repo_root)
    return git_output(repo_root, "ls-files", "--", relpath) is not None


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


@dataclass(frozen=True)
class _Unreadable:
    reason: str
    has_marker: bool


def _read_lines(path: Path) -> list[str] | _Unreadable:
    try:
        raw = path.read_bytes()
    except OSError as error:
        # Without the bytes there is no telling whether a block is there, so report it.
        return _Unreadable(reason=error.strerror or str(error), has_marker=True)
    try:
        # Decoding the bytes keeps CRLF intact, which read_text's newline translation would not.
        return raw.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        # The markers are ASCII, so a file without them has no block whatever its encoding.
        has_marker = BLOCK_BEGIN.encode() in raw or BLOCK_END.encode() in raw
        return _Unreadable(reason="it does not decode as UTF-8", has_marker=has_marker)


def _writable(path: Path) -> bool:
    # The atomic rewrite replaces the file from a temp file beside it, so both must be writable.
    target = path.resolve()
    return os.access(target, os.W_OK) and os.access(target.parent, os.W_OK)


def _find_block(lines: list[str]) -> _Block | Literal["damaged"] | None:
    # git skips a UTF-8 byte-order mark before the first line, so the markers must too.
    texts = [
        line.rstrip("\r\n").removeprefix(BYTE_ORDER_MARK) if index == 0 else line.rstrip("\r\n")
        for index, line in enumerate(lines)
    ]
    begins = [index for index, text in enumerate(texts) if text == BLOCK_BEGIN]
    ends = [index for index, text in enumerate(texts) if text == BLOCK_END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        return "damaged"
    begin, end = begins[0], ends[0]
    return _Block(begin, end, missing=_missing_patterns(lines[begin + 1 : end]))


# A private block hides all of `.byor/`; a user may spell that line any of these ways.
_PRIVATE_DIRECTORY_LINES = frozenset({".byor", ".byor/", ".byor/**"})
_PRIVATE_LINES = _PRIVATE_DIRECTORY_LINES | {"sgconfig.yml"}


def _missing_patterns(block_lines: list[str]) -> tuple[str, ...] | None:
    # Comments, blank lines and the user's own patterns say nothing about the setup. A leading
    # slash only anchors a pattern at the repo root, which is where byor's paths are anyway.
    lines = {
        line.strip().removeprefix("/") for line in block_lines if line.strip() and not line.lstrip().startswith("#")
    }
    private = bool(lines & _PRIVATE_LINES)
    shared = bool(lines & set(IGNORED_PATTERNS))
    if private == shared:
        return None
    if private:
        needed = [] if lines & _PRIVATE_DIRECTORY_LINES else [".byor/"]
        return tuple(needed + ([] if "sgconfig.yml" in lines else ["sgconfig.yml"]))
    return tuple(pattern for pattern in IGNORED_PATTERNS if pattern not in lines)


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
    A damaged block raises ConfigError instead of being rewritten, because byor
    cannot tell which of the lines after a stray marker are the user's.
    """
    if private and not (repo_root / ".git").exists():
        msg = f"{repo_root} has no .git directory; cannot use private mode"
        raise ConfigError(msg)
    path = ignore_file(repo_root, private=private)
    try:
        # Decoding the bytes keeps CRLF intact, which read_text's newline translation would not.
        current = path.read_bytes().decode("utf-8") if path.is_file() else ""
    except UnicodeDecodeError as error:
        msg = f"cannot read {path} as UTF-8 ({error.reason}); save it as UTF-8, then run byor init again"
        raise ConfigError(msg) from error
    bom = BYTE_ORDER_MARK if current.startswith(BYTE_ORDER_MARK) else ""
    lines = current.removeprefix(bom).splitlines(keepends=True)
    block = _find_block(lines)
    if block == "damaged":
        msg = (
            f"the byor ignore block in {path} is damaged: it needs exactly one begin marker and one"
            " end marker after it; fix the markers by hand, then run byor init again"
        )
        raise ConfigError(msg)
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    updated = bom + _with_block(lines, existing=block, patterns=_patterns(private=private), newline=newline)
    if updated == current:
        return False
    write_text_atomic(path, updated)
    return True


def _patterns(*, private: bool) -> tuple[str, ...]:
    return PRIVATE_IGNORED_PATTERNS if private else IGNORED_PATTERNS


def _with_block(lines: list[str], *, existing: _Block | None, patterns: tuple[str, ...], newline: str) -> str:
    block = newline.join((BLOCK_BEGIN, *patterns, BLOCK_END)) + newline
    if existing is not None:
        return "".join(lines[: existing.begin]) + block + "".join(lines[existing.end + 1 :])
    content = "".join(lines)
    if content and not content.endswith("\n"):
        content += newline
    separator = newline if content else ""
    return content + separator + block


def has_private_block(repo_root: Path) -> bool:
    """Report whether `.git/info/exclude` holds a well-formed private byor block.

    init uses this to keep a private repo private when `--private` is not given: falling back to
    the shared default would write a `.gitignore` block that git then offers to commit. The exclude
    file lives in the common git dir, so every worktree of a private repo sees the same answer.
    """
    try:
        path = ignore_file(repo_root, private=True)
    except ConfigError:
        return False
    if not path.is_file():
        return False
    lines = _read_lines(path)
    if isinstance(lines, _Unreadable):
        return False
    block = _find_block(lines)
    return isinstance(block, _Block) and _lists_private_directory(lines[block.begin + 1 : block.end])


@dataclass(frozen=True)
class RemovedPrivateBlock:
    kept_lines: int
    """Lines the user added inside the block, left in the file where the block was."""


def remove_private_block(repo_root: Path) -> RemovedPrivateBlock | Literal["damaged"] | None:
    """Delete a private byor block from `.git/info/exclude`, for a repo that is turning shared.

    A private init hides all of `.byor/` and `sgconfig.yml` through the exclude file, which every
    worktree shares. A later shared init writes its block to `.gitignore` but would otherwise leave
    that one in place, so the team config, the promoted rules and `sgconfig.yml` stay ignored and a
    gate gets committed without them. Only a well-formed block listing `.byor/` is removed; a shared
    block that byor 0.2.0 put there stays. The markers and byor's own lines go, and any other line
    inside the block stays where it was, so a file the user hid there stays hidden. Returns
    "damaged" when the markers do not pair up, so the caller can tell the user to remove the block
    by hand.
    """
    try:
        path = ignore_file(repo_root, private=True)
    except ConfigError:
        return None
    if not path.is_file():
        return None
    lines = _read_lines(path)
    if isinstance(lines, _Unreadable):
        return None
    block = _find_block(lines)
    if block == "damaged":
        return "damaged"
    if block is None or not _lists_private_directory(lines[block.begin + 1 : block.end]):
        return None
    kept = [
        line
        for line in lines[block.begin + 1 : block.end]
        if line.strip() and line.strip().removeprefix("/") not in _PRIVATE_LINES
    ]
    # A byte-order mark on the begin marker's line belongs to the file, not to the block.
    bom = BYTE_ORDER_MARK if block.begin == 0 and lines[0].startswith(BYTE_ORDER_MARK) else ""
    write_text_atomic(path, bom + "".join([*lines[: block.begin], *kept, *lines[block.end + 1 :]]))
    return RemovedPrivateBlock(kept_lines=len(kept))


def _lists_private_directory(block_lines: list[str]) -> bool:
    return any(line.strip().removeprefix("/") in _PRIVATE_DIRECTORY_LINES for line in block_lines)
