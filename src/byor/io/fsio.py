"""Atomic text file writes (write temp, flush, rename into place)."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Literal

NEW_FILE_MODE = 0o666

# The notice is load-bearing for ownership detection; markers in
# other comment dialects must derive from it so they stay in lockstep.
MANAGED_NOTICE = "Managed by BYOR. Manual edits may be overwritten."

MANAGED_MARKER = f"<!-- {MANAGED_NOTICE} -->"

MarkedWriteResult = Literal["written", "unchanged", "unmarked"]

MarkedTextStatus = Literal["missing", "unmarked", "unchanged", "drifted"]


def marked_text_status(path: Path, content: str, marker: str) -> MarkedTextStatus:
    """Classify a path against the content a managed write would produce.

    Files without the marker are user-owned; marker-bearing files that differ
    from `content` have drifted and need a rewrite.
    """
    if not path.is_file():
        return "missing"
    existing = path.read_text(encoding="utf-8")
    if marker not in existing:
        return "unmarked"
    if existing == content:
        return "unchanged"
    return "drifted"


def write_marked_text(path: Path, content: str, marker: str) -> MarkedWriteResult:
    """Converge a BYOR-managed file to `content`.

    Files without the marker are user-owned and never touched.
    """
    status = marked_text_status(path, content, marker)
    if status == "unmarked" or status == "unchanged":
        return status
    write_text_atomic(path, content)
    return "written"


def write_text_atomic(path: Path, content: str) -> None:
    """Write via a temp file in the same directory, flush, then rename into place.

    Overwrites keep the destination's existing permissions; new files honor
    the umask (mkstemp would otherwise leave everything at 0600).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, _destination_mode(path))
        os.replace(temp_name, path)
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
