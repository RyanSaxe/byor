"""Take turns on one read-modify-write across concurrent byor processes.

byor runs from agent hooks and from commands at the same time, and every file it writes is replaced
whole through a rename. That keeps each file intact, but two processes that read, change and write
the same file still lose one change. A lock file created with `O_CREAT | O_EXCL` works on every OS
byor supports. A lock older than `stale_seconds` was left by a process that died while holding it,
so the next process takes it over instead of waiting forever.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = (
    "acquire_lock",
    "file_lock",
)

POLL_SECONDS = 0.05


def acquire_lock(lock: Path, *, wait_seconds: float, stale_seconds: float) -> bool:
    """Create the lock file `lock`, polling while another process holds it.

    Returns True once this process created the file, and False when `wait_seconds` pass first. A
    lock whose modification time is more than `stale_seconds` old is deleted and taken, because
    its holder died mid-write. The caller deletes the file to release it. Raises OSError when the
    file cannot be created at all, for example in a directory the user cannot write, so callers can
    report that as an unwritable state directory rather than as a busy lock.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            if _is_stale(lock, stale_seconds=stale_seconds):
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_SECONDS)
        else:
            return True


@contextmanager
def file_lock(lock: Path, *, wait_seconds: float, stale_seconds: float) -> Iterator[bool]:
    """Hold `lock` for the body of a `with` block, and delete it afterwards even on error.

    Yields True when `acquire_lock` took the lock and False when the wait ran out, in which case
    the body runs without the lock and must decide itself whether to skip its work, as the
    worktree copy does when another hook is already copying:

        with file_lock(git_dir / "byor-copy.lock", wait_seconds=5, stale_seconds=30) as held:
            if not held:
                return None

    An OSError from creating the lock propagates before the body runs.
    """
    if not acquire_lock(lock, wait_seconds=wait_seconds, stale_seconds=stale_seconds):
        yield False
        return
    try:
        yield True
    finally:
        lock.unlink(missing_ok=True)


def _is_stale(lock: Path, *, stale_seconds: float) -> bool:
    try:
        return time.time() - lock.stat().st_mtime > stale_seconds
    except FileNotFoundError:
        return True
