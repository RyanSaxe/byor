from pathlib import Path

import pytest

from byor.errors import ConfigError
from byor.ignore import (
    IGNORED_PATTERNS,
    rule_visibility_ok,
    write_ignore_block,
    write_rule_visibility_file,
)


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
    gitignore.write_text(pristine.replace(".byor/local.yml\n", ""))

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


def test_ignored_patterns_cover_nested_rule_files() -> None:
    """`*` does not cross `/` in gitignore; synced copies keep their nesting."""
    assert ".byor/rules/personal/global/**/*.yml" in IGNORED_PATTERNS
    assert ".byor/rules/personal/local/**/*.yml" in IGNORED_PATTERNS


def test_visibility_file_is_written_idempotently_and_satisfies_the_check(
    tmp_path: Path,
) -> None:
    assert rule_visibility_ok(tmp_path) is False
    assert write_rule_visibility_file(tmp_path) == "written"
    assert write_rule_visibility_file(tmp_path) == "unchanged"
    assert rule_visibility_ok(tmp_path) is True


def test_unmarked_visibility_file_is_preserved(tmp_path: Path) -> None:
    (tmp_path / ".ignore").write_text("!*.yml\n")

    assert write_rule_visibility_file(tmp_path) == "unmarked"

    assert (tmp_path / ".ignore").read_text() == "!*.yml\n"
    assert rule_visibility_ok(tmp_path) is False  # missing !*.yaml
