"""Give a linked git worktree the byor files that its main checkout keeps out of git.

`git worktree add` checks out only tracked files, and byor keeps a repo's personal files untracked:
all of `.byor/` and `sgconfig.yml` under a private init, `.byor/local.yml` and the rule mirrors under
a committed one. Without them a new worktree runs only the global rules, or no rules at all, until
someone runs `byor init` there again. Only files git ignores in both checkouts are copied, so a copy
can never be committed and never brings back a tracked rule the worktree's branch deleted.
"""

from __future__ import annotations

import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from byor.config import load_repo_config, local_config_path, repo_config_path
from byor.io.gitio import git_stdout

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = (
    "PendingCopy",
    "copy_from_main_checkout",
    "main_checkout_root",
    "pending_copy",
)

GITDIR_PREFIX = "gitdir: "
LOCK_NAME = "byor-copy.lock"
LOCK_WAIT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.05
# A copy takes milliseconds, so a lock this old was left by a process that died mid-copy.
STALE_LOCK_SECONDS = 30.0


@dataclass(frozen=True)
class PendingCopy:
    """The byor files a linked worktree still needs from its main checkout.

    `relpaths` are relative to both checkouts, in copy order: `.byor/local.yml` comes last because
    its presence is what tells every later command and hook that the copy finished, and
    `.byor/config.yml` just before it, so a copy that fails partway never leaves a private worktree
    looking initialized without its sgconfig.yml. Doctor and `byor sync --check` read this to
    report a worktree that has not copied yet.
    """

    main_root: Path
    relpaths: list[str]


def main_checkout_root(worktree_root: Path) -> Path | None:
    """Return the main checkout of a linked worktree, or None for any other directory.

    A linked worktree's `.git` is a file naming the worktree's own git dir, and that dir's
    `commondir` file leads to the repository all of its worktrees share. The main checkout is the
    directory holding that shared `.git`. Reading the two files keeps a git subprocess off every
    hook call. A bare repository has no main checkout to copy from, so it returns None.
    """
    git_dir = _linked_git_dir(worktree_root)
    if git_dir is None:
        return None
    commondir_file = git_dir / "commondir"
    if not commondir_file.is_file():
        return None
    common_dir = (git_dir / commondir_file.read_text(encoding="utf-8").strip()).resolve()
    if common_dir.name != ".git":
        return None
    return common_dir.parent


def pending_copy(repo_root: Path) -> PendingCopy | None:
    """Return the byor files a linked worktree still needs from its main checkout, or None.

    `.byor/local.yml` marks a finished copy, because `byor init` always writes it, git never tracks
    it, and the copy writes it last; checking it costs one stat in every initialized checkout. Past
    that, a file qualifies only when git ignores it in the main checkout and would ignore it in the
    worktree too. A worktree whose branch predates byor's ignore block therefore gets nothing, since
    a copy there could be committed. Raises ConfigError for a malformed main `.byor/config.yml`.
    """
    if local_config_path(repo_root).is_file():
        return None
    main_root = main_checkout_root(repo_root)
    if main_root is None or not repo_config_path(main_root).is_file():
        return None
    sgconfig = load_repo_config(main_root).paths.sgconfig
    ignored_in_main = _nul_split(
        git_stdout(main_root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", ".byor", sgconfig)
    )
    missing = [relpath for relpath in ignored_in_main if not (repo_root / relpath).exists()]
    if not missing:
        return None
    ignored_here = set(
        _nul_split(git_stdout(repo_root, "check-ignore", "--stdin", "-z", stdin="\0".join(missing) + "\0"))
    )
    marker = local_config_path(repo_root).relative_to(repo_root).as_posix()
    config = repo_config_path(repo_root).relative_to(repo_root).as_posix()
    relpaths = sorted(
        (relpath for relpath in missing if relpath in ignored_here),
        key=lambda relpath: (relpath == marker, relpath == config),
    )
    if not relpaths:
        return None
    return PendingCopy(main_root=main_root, relpaths=relpaths)


def copy_from_main_checkout(repo_root: Path) -> PendingCopy | None:
    """Copy what `pending_copy` names into the worktree, under a lock; return what was copied.

    Hooks fire in parallel, so the copy holds a lock file in the worktree's own git dir, writes each
    file whole through a rename, and writes the local.yml marker last. A process that finds the lock
    held waits for the copy to finish and then finds nothing pending; after a few seconds it gives
    up and returns None, leaving the worktree to the holder. A copy that fails partway leaves no
    marker, so the next command or hook retries it.
    """
    git_dir = _linked_git_dir(repo_root)
    if git_dir is None or pending_copy(repo_root) is None:
        return None
    with _copy_lock(git_dir) as held:
        if not held:
            return None
        pending = pending_copy(repo_root)
        if pending is None:
            return None
        for relpath in pending.relpaths:
            _copy_whole(pending.main_root / relpath, repo_root / relpath)
    return pending


def _linked_git_dir(worktree_root: Path) -> Path | None:
    dot_git = worktree_root / ".git"
    if not dot_git.is_file():
        return None
    pointer = dot_git.read_text(encoding="utf-8").strip()
    if not pointer.startswith(GITDIR_PREFIX):
        return None
    return (worktree_root / pointer.removeprefix(GITDIR_PREFIX)).resolve()


def _nul_split(output: str | None) -> list[str]:
    return [entry for entry in (output or "").split("\0") if entry]


def _copy_whole(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.byor-copy-{os.getpid()}")
    shutil.copy2(source, partial)
    partial.replace(target)


@contextmanager
def _copy_lock(git_dir: Path) -> Iterator[bool]:
    lock = git_dir / LOCK_NAME
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            if _lock_is_stale(lock):
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                yield False
                return
            time.sleep(LOCK_POLL_SECONDS)
    try:
        yield True
    finally:
        lock.unlink(missing_ok=True)


def _lock_is_stale(lock: Path) -> bool:
    try:
        return time.time() - lock.stat().st_mtime > STALE_LOCK_SECONDS
    except FileNotFoundError:
        return True
