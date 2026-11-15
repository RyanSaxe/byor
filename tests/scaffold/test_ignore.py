"""Exercise BYOR ignore-file scaffolding.

Shared mode appends a managed block to .gitignore — idempotent, healed if edited — while private
mode writes .git/info/exclude, resolving the real location inside a worktree. The `.ignore`
visibility file is the subtle part: it negates the gitignore patterns so ast-grep can still load the
ignored personal rule copies.
"""

from pathlib import Path

import pytest
from support import git, write_global_command_rule

from byor.cli import main
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


# Mirror directories are wholly byor-owned, so a user `.ignore` that hides the
# rules from ast-grep is reclaimed there — otherwise doctor's rules_visible
# remediation could never converge.
def test_force_rewrites_a_user_owned_visibility_file_that_hides_rules(tmp_path: Path) -> None:
    (tmp_path / ".ignore").write_text("# my own ignore file\n")

    assert write_rule_visibility_file(tmp_path, force=True) == "written"

    assert rule_visibility_ok(tmp_path) is True


def test_force_preserves_a_user_owned_visibility_file_that_works(tmp_path: Path) -> None:
    content = "my-extra-pattern\n!*.yml\n!*.yaml\n"
    (tmp_path / ".ignore").write_text(content)

    assert write_rule_visibility_file(tmp_path, force=True) == "unmarked"

    assert (tmp_path / ".ignore").read_text() == content


# The block byor wrote before command rules existed, as an older init left it.
BLOCK_BEFORE_COMMAND_RULES = """\
# >>> Managed by BYOR. Manual edits may be overwritten. >>>
.byor/local.yml
.byor/rules/personal/local/**/*.yml
.byor/rules/personal/local/**/*.yaml
.byor/rules/personal/global/**/*.yml
.byor/rules/personal/global/**/*.yaml
.byor/rules/personal/packages/**/*.yml
.byor/rules/personal/packages/**/*.yaml
# <<< Managed by BYOR <<<
"""


def shared_repo_with_an_old_block(home: Path) -> Path:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--no-private", "--no-git-hooks"]) == 0
    (repo / ".gitignore").write_text("node_modules/\n\n" + BLOCK_BEFORE_COMMAND_RULES)
    return repo


def test_a_self_healing_command_rewrites_an_ignore_block_that_predates_command_rules(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_global_command_rule(home, "no-pip.yml", rule_id="no-pip")
    repo = shared_repo_with_an_old_block(home)
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    assert "byor: updated the byor ignore block in .gitignore" in capsys.readouterr().err
    gitignore = (repo / ".gitignore").read_text()
    assert gitignore.startswith("node_modules/\n")
    assert ".byor/commands/personal/global/**/*.yml" in gitignore
    assert "no-pip.yml" not in git(repo, "status", "--short", "--untracked-files=all")


def test_byor_sync_rewrites_an_ignore_block_that_predates_command_rules(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = shared_repo_with_an_old_block(home)
    capsys.readouterr()

    assert main(["sync", "--repo", str(repo)]) == 0

    assert "Updated the byor ignore block in .gitignore" in capsys.readouterr().out
    assert all(pattern in (repo / ".gitignore").read_text() for pattern in IGNORED_PATTERNS)


def test_self_heal_never_adds_a_block_to_an_ignore_file_without_one(home: Path) -> None:
    repo = shared_repo_with_an_old_block(home)
    (repo / ".gitignore").write_text("node_modules/\n")

    assert main(["list", "--repo", str(repo)]) == 0

    assert (repo / ".gitignore").read_text() == "node_modules/\n"


def test_byor_sync_rewrites_an_outdated_private_block(home: Path) -> None:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--private", "--no-git-hooks"]) == 0
    exclude = ignore_file(repo, private=True)
    exclude.write_text(exclude.read_text().replace("sgconfig.yml\n", ""))

    assert main(["sync", "--repo", str(repo)]) == 0

    assert all(pattern in exclude.read_text() for pattern in PRIVATE_IGNORED_PATTERNS)
