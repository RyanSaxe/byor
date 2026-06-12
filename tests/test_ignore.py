from pathlib import Path

import pytest

from byolsp.errors import ConfigError
from byolsp.ignore import IGNORED_PATTERNS, write_ignore_block


def test_project_mode_appends_block_preserving_existing_entries(
    tmp_path: Path,
) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n")

    assert write_ignore_block(tmp_path, "project") is True

    content = gitignore.read_text()
    assert content.startswith("node_modules/\n")
    assert all(pattern in content for pattern in IGNORED_PATTERNS)


def test_project_mode_is_idempotent(tmp_path: Path) -> None:
    write_ignore_block(tmp_path, "project")
    first = (tmp_path / ".gitignore").read_text()

    assert write_ignore_block(tmp_path, "project") is False
    assert (tmp_path / ".gitignore").read_text() == first


def test_edits_inside_the_block_are_healed(tmp_path: Path) -> None:
    write_ignore_block(tmp_path, "project")
    gitignore = tmp_path / ".gitignore"
    pristine = gitignore.read_text()
    gitignore.write_text(pristine.replace(".byolsp/local.yml\n", ""))

    assert write_ignore_block(tmp_path, "project") is True
    assert gitignore.read_text() == pristine


def test_local_mode_writes_git_info_exclude(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    assert write_ignore_block(tmp_path, "local") is True

    content = (tmp_path / ".git" / "info" / "exclude").read_text()
    assert all(pattern in content for pattern in IGNORED_PATTERNS)
    assert not (tmp_path / ".gitignore").exists()


def test_local_mode_requires_a_git_repository(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no .git directory"):
        write_ignore_block(tmp_path, "local")
