"""Exercise BYOR ignore-file scaffolding.

Shared mode appends a managed block to .gitignore — idempotent, healed if edited — while private
mode writes .git/info/exclude, resolving the real location inside a worktree. The `.ignore`
visibility file is the subtle part: it negates the gitignore patterns so ast-grep can still load the
ignored personal rule copies.
"""

from pathlib import Path

import pytest
from support import git

from byor.errors import ConfigError
from byor.scaffold.ignore import (
    IGNORED_PATTERNS,
    PRIVATE_IGNORED_PATTERNS,
    ignore_file,
    rule_visibility_ok,
    write_ignore_block,
    write_rule_visibility_file,
)


def test_shared_mode_appends_block_preserving_existing_entries(
    tmp_path: Path,
) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n")

    assert write_ignore_block(tmp_path, private=False) is True

    content = gitignore.read_text()
    assert content.startswith("node_modules/\n")
    assert all(pattern in content for pattern in IGNORED_PATTERNS)


def test_shared_mode_is_idempotent(tmp_path: Path) -> None:
    write_ignore_block(tmp_path, private=False)
    first = (tmp_path / ".gitignore").read_text()

    assert write_ignore_block(tmp_path, private=False) is False
    assert (tmp_path / ".gitignore").read_text() == first


def test_edits_inside_the_block_are_healed(tmp_path: Path) -> None:
    write_ignore_block(tmp_path, private=False)
    gitignore = tmp_path / ".gitignore"
    pristine = gitignore.read_text()
    gitignore.write_text(pristine.replace(".byor/local.yml\n", ""))

    assert write_ignore_block(tmp_path, private=False) is True
    assert gitignore.read_text() == pristine


def test_private_mode_writes_git_info_exclude(tmp_path: Path) -> None:
    git(tmp_path, "init", "--quiet")

    assert write_ignore_block(tmp_path, private=True) is True

    content = (tmp_path / ".git" / "info" / "exclude").read_text()
    assert all(pattern in content for pattern in PRIVATE_IGNORED_PATTERNS)
    assert not (tmp_path / ".gitignore").exists()


def test_private_mode_resolves_info_exclude_in_a_worktree(tmp_path: Path) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    git(main_repo, "init", "--quiet")
    git(main_repo, "commit", "--allow-empty", "-q", "-m", "init")
    worktree = tmp_path / "worktree"
    git(main_repo, "worktree", "add", "-q", str(worktree))

    assert write_ignore_block(worktree, private=True) is True

    # `.git` in the worktree is a file; info/exclude lives in the common git dir.
    exclude = ignore_file(worktree, private=True)
    assert exclude.read_text()
    assert all(pattern in exclude.read_text() for pattern in PRIVATE_IGNORED_PATTERNS)


def test_private_mode_requires_a_git_repository(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"no \.git directory"):
        write_ignore_block(tmp_path, private=True)


def test_ignored_patterns_cover_nested_rule_files() -> None:
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
