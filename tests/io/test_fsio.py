import os
import stat
import sys
from pathlib import Path

import pytest

from byor.io.fsio import write_text_atomic


def test_write_text_atomic_creates_parents_overwrites_and_leaves_no_temp_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "file.txt"

    write_text_atomic(path, "first")
    write_text_atomic(path, "second")

    assert path.read_text() == "second"
    assert [entry.name for entry in path.parent.iterdir()] == ["file.txt"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX mode bits and umask are not meaningful"
)
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
