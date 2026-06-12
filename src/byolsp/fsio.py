"""Atomic text file writes (SPEC 17: write temp, flush, rename into place)."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

NEW_FILE_MODE = 0o666


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
