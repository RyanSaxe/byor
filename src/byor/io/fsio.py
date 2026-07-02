"""Write BYOR-managed text files atomically.

Managed files need marker-aware updates that avoid clobbering user-owned content and preserve file
permissions when rewritten. This module owns those low-level write and cleanup primitives so
command and scaffold code can stay declarative.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Literal

__all__ = (
    "marked_text_status",
    "prune_empty_dirs",
    "write_marked_text",
    "write_text_atomic",
)

NEW_FILE_MODE = 0o666

# The notice is load-bearing for ownership detection; markers in
# other comment dialects must derive from it so they stay in lockstep.
MANAGED_NOTICE = "Managed by BYOR. Manual edits may be overwritten."

MANAGED_MARKER = f"<!-- {MANAGED_NOTICE} -->"

MarkedWriteResult = Literal["written", "unchanged", "unmarked"]

MarkedTextStatus = Literal["missing", "unmarked", "unchanged", "drifted"]


def marked_text_status(path: Path, content: str, *, marker: str) -> MarkedTextStatus:
    if not path.is_file():
        return "missing"
    existing = path.read_text(encoding="utf-8")
    if marker not in existing:
        return "unmarked"
    if existing == content:
        return "unchanged"
    return "drifted"


def write_marked_text(path: Path, content: str, *, marker: str) -> MarkedWriteResult:
    status = marked_text_status(path, content, marker=marker)
    if status == "unmarked":
        return "unmarked"
    if status == "unchanged":
        return "unchanged"
    write_text_atomic(path, content)
    return "written"


def prune_empty_dirs(root: Path, *, keep_root: bool = True) -> None:
    """Remove empty directories below `root`, deepest first.

    Sync uses the default to keep a mirror directory in place after its last
    rule is deleted; uninstall passes `keep_root=False` to remove the whole
    skill tree once emptied. A missing `root` is a no-op either way.
    """
    # os.walk snapshots entries before children are pruned, so re-check emptiness.
    for dirpath, _, _ in os.walk(root, topdown=False):
        directory = Path(dirpath)
        if (keep_root and directory == root) or any(directory.iterdir()):
            continue
        directory.rmdir()


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        # newline="\n" so generated files (notably /bin/sh hook shims) never
        # pick up CRLF from Windows' default newline translation.
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temp_name).chmod(_destination_mode(path))
        Path(temp_name).replace(path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _destination_mode(path: Path) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return NEW_FILE_MODE & ~umask
