"""Exercise atomic and managed file writes.

write_text_atomic underpins every file byor owns, so the contract is pinned precisely: parents
created, the destination replaced atomically, and no temp file left behind on the way. Permission
behavior matters too — an overwrite preserves the existing mode while a fresh file honors the umask.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

from byor.io.fsio import marked_text_status, write_marked_text, write_text_atomic


def test_write_text_atomic_creates_parents_overwrites_and_leaves_no_temp_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "file.txt"

    write_text_atomic(path, "first")
    write_text_atomic(path, "second")

    assert path.read_text() == "second"
    assert [entry.name for entry in path.parent.iterdir()] == ["file.txt"]


# A zero-byte managed file has no user content to protect; classifying it as
# unmarked (user-owned) made a truncated skill or hook permanently unhealable.
def test_marked_text_status_treats_an_empty_file_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.touch()

    assert marked_text_status(path, "<!-- m -->\ncontent\n", marker="<!-- m -->") == "missing"
    assert write_marked_text(path, "<!-- m -->\ncontent\n", marker="<!-- m -->") == "written"
    assert path.read_text() == "<!-- m -->\ncontent\n"


def test_write_text_atomic_writes_lf_bytes_on_every_platform(tmp_path: Path) -> None:
    # Generated /bin/sh hook shims break under CRLF, so newline translation
    # must stay off; on Windows the default text mode would write \r\n.
    path = tmp_path / "hook.sh"

    write_text_atomic(path, "#!/bin/sh\nexit 0\n")

    assert path.read_bytes() == b"#!/bin/sh\nexit 0\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits and umask are not meaningful")
def test_write_text_atomic_preserves_mode_on_overwrite_and_honors_umask(
    tmp_path: Path,
) -> None:
    previous_umask = os.umask(0o022)
    try:
        fresh = tmp_path / "fresh.txt"
        write_text_atomic(fresh, "new files honor the umask")
        assert stat.S_IMODE(fresh.stat().st_mode) == 0o644

        hook = tmp_path / "hook.sh"
        hook.write_text("#!/bin/sh\n")
        hook.chmod(0o755)
        write_text_atomic(hook, "#!/bin/sh\nupdated\n")
        assert stat.S_IMODE(hook.stat().st_mode) == 0o755
    finally:
        os.umask(previous_umask)
