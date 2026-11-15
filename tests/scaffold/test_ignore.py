"""Exercise BYOR ignore-file scaffolding.

Shared mode appends a managed block to .gitignore — idempotent, healed if edited — while private
mode writes .git/info/exclude, resolving the real location inside a worktree. The `.ignore`
visibility file is the subtle part: it negates the gitignore patterns so ast-grep can still load the
ignored personal rule copies.
"""

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from support import git, write_global_command_rule

from byor.cli import main
from byor.errors import ConfigError
from byor.rules.sync import STALE_EXIT_CODE
from byor.scaffold.ignore import (
    BLOCK_BEGIN,
    BLOCK_END,
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

    assert f"Updated the byor ignore block in {repo / '.gitignore'}" in capsys.readouterr().out
    assert all(pattern in (repo / ".gitignore").read_text() for pattern in IGNORED_PATTERNS)


# A post-checkout shim that rewrote it would leave the work tree dirty and block the next checkout.
def test_sync_run_by_git_leaves_a_tracked_gitignore_alone(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = shared_repo_with_an_old_block(home)
    git(repo, "add", ".gitignore")
    before = (repo / ".gitignore").read_bytes()
    monkeypatch.setenv("GIT_PREFIX", "")

    assert main(["sync", "--repo", str(repo)]) == 0

    assert (repo / ".gitignore").read_bytes() == before


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


CURRENT_BLOCK = "\n".join((BLOCK_BEGIN, *IGNORED_PATTERNS, BLOCK_END))


@pytest.mark.parametrize(
    ("content", "doctor_says"),
    [
        pytest.param(
            f"{BLOCK_BEGIN}\nsecrets.env\n{BLOCK_BEFORE_COMMAND_RULES}".encode(),
            "is damaged",
            id="unterminated-block-before-a-complete-one",
        ),
        pytest.param(b"# r\xe9sultats\n" + BLOCK_BEFORE_COMMAND_RULES.encode(), "as UTF-8", id="latin-1"),
        pytest.param(b"# r\xe9sultats\nnode_modules/\n", "is gone", id="latin-1-without-a-block"),
        pytest.param(CURRENT_BLOCK.encode(), "are git-ignored", id="current-block-without-final-newline"),
    ],
)
def test_self_heal_leaves_a_block_it_cannot_safely_rewrite_byte_for_byte(
    home: Path, *, capsys: pytest.CaptureFixture[str], content: bytes, doctor_says: str
) -> None:
    repo = shared_repo_with_an_old_block(home)
    (repo / ".gitignore").write_bytes(content)

    assert main(["list", "--repo", str(repo)]) == 0
    capsys.readouterr()
    main(["doctor", "--repo", str(repo), "--quick"])

    assert (repo / ".gitignore").read_bytes() == content
    assert doctor_says in capsys.readouterr().out


def test_refresh_rewrites_only_the_block_and_keeps_crlf_line_endings(home: Path) -> None:
    repo = shared_repo_with_an_old_block(home)
    (repo / ".gitignore").write_bytes(
        f"node_modules/\n{BLOCK_BEFORE_COMMAND_RULES}dist/\n".replace("\n", "\r\n").encode()
    )

    assert main(["list", "--repo", str(repo)]) == 0

    expected = f"node_modules/\n{CURRENT_BLOCK}\ndist/\n".replace("\n", "\r\n").encode()
    assert (repo / ".gitignore").read_bytes() == expected


def test_a_shared_block_in_info_exclude_stays_shared(home: Path) -> None:
    repo = shared_repo_with_an_old_block(home)
    (repo / ".gitignore").write_text("node_modules/\n")
    exclude = ignore_file(repo, private=True)
    exclude.write_text(BLOCK_BEFORE_COMMAND_RULES)

    assert main(["list", "--repo", str(repo)]) == 0

    assert exclude.read_text() == f"{CURRENT_BLOCK}\n"


def test_sync_check_counts_an_outdated_block_as_stale(home: Path) -> None:
    repo = shared_repo_with_an_old_block(home)

    assert main(["sync", "--check", "--repo", str(repo)]) == STALE_EXIT_CODE


def test_sync_leaves_the_block_of_an_uninitialized_repo_alone(home: Path) -> None:
    repo = shared_repo_with_an_old_block(home)
    shutil.rmtree(repo / ".byor")

    assert main(["sync", "--repo", str(repo)]) != 0

    assert (repo / ".gitignore").read_text() == "node_modules/\n\n" + BLOCK_BEFORE_COMMAND_RULES


def private_repo(home: Path) -> Path:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--private", "--no-git-hooks"]) == 0
    return repo


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda text: text.replace(".byor/local.yml\n", ".byor/local.yml\n.env\n"), id="user-line-inside"),
        pytest.param(lambda text: text.replace(".byor/local.yml\n", "# mine\n.byor/local.yml\n"), id="comment-inside"),
    ],
)
def test_self_heal_keeps_a_current_block_a_user_added_lines_to(home: Path, edit: Callable[[str], str]) -> None:
    repo = shared_repo_with_an_old_block(home)
    gitignore = repo / ".gitignore"
    gitignore.write_text(edit(f"node_modules/\n{CURRENT_BLOCK}\n"))
    before = gitignore.read_bytes()

    assert main(["list", "--repo", str(repo)]) == 0

    assert gitignore.read_bytes() == before


def test_self_heal_keeps_a_hand_edited_private_block_private(home: Path) -> None:
    repo = private_repo(home)
    exclude = ignore_file(repo, private=True)
    exclude.write_text(exclude.read_text().replace(".byor/\n", "/.byor/\n"))

    assert main(["list", "--repo", str(repo)]) == 0

    assert ".byor/local.yml" not in exclude.read_text()
    assert git(repo, "status", "--short") == ""


def test_doctor_reports_an_outdated_block_even_beside_a_current_one(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = shared_repo_with_an_old_block(home)
    (repo / ".gitignore").write_text(f"{CURRENT_BLOCK}\n")
    ignore_file(repo, private=True).write_text(BLOCK_BEFORE_COMMAND_RULES)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    assert "the byor ignore block in .git/info/exclude is out of date; run `byor sync`" in capsys.readouterr().out


def test_self_heal_writes_a_symlinked_gitignore_through_to_its_target(home: Path) -> None:
    repo = shared_repo_with_an_old_block(home)
    target = home / "shared.gitignore"
    target.write_text(BLOCK_BEFORE_COMMAND_RULES)
    (repo / ".gitignore").unlink()
    (repo / ".gitignore").symlink_to(target)

    assert main(["list", "--repo", str(repo)]) == 0

    assert (repo / ".gitignore").is_symlink()
    assert target.read_text() == f"{CURRENT_BLOCK}\n"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as a non-root user")
def test_an_unwritable_outdated_block_is_left_alone_and_named(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = shared_repo_with_an_old_block(home)
    gitignore = repo / ".gitignore"
    before = gitignore.read_bytes()
    gitignore.chmod(0o444)
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0
    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    assert gitignore.read_bytes() == before
    captured = capsys.readouterr()
    assert "byor: " not in captured.err
    assert "is out of date and the file is not writable" in captured.out


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(f"{BLOCK_BEGIN}\n.byor/local.yml\nsecrets.env\ndist/\n", id="no-end-marker"),
        pytest.param(f"{BLOCK_BEGIN}\nsecrets.env\n{CURRENT_BLOCK}\ndist/\n", id="stray-begin-before-a-good-block"),
    ],
)
def test_init_refuses_a_damaged_block_and_keeps_the_user_lines(home: Path, content: str) -> None:
    repo = shared_repo_with_an_old_block(home)
    (repo / ".gitignore").write_text(content)

    with pytest.raises(ConfigError, match="is damaged"):
        write_ignore_block(repo, private=False)

    assert (repo / ".gitignore").read_text() == content


def test_a_block_after_a_byte_order_mark_counts_as_the_block(home: Path) -> None:
    repo = shared_repo_with_an_old_block(home)
    content = f"﻿{CURRENT_BLOCK}\n"
    (repo / ".gitignore").write_text(content, encoding="utf-8")

    assert write_ignore_block(repo, private=False) is False
    assert (repo / ".gitignore").read_text(encoding="utf-8") == content


def test_init_writes_the_block_in_the_files_own_line_endings(home: Path) -> None:
    repo = home / "crlf"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_bytes(b"node_modules/\r\n")

    assert main(["init", "--repo", str(repo), "--non-interactive", "--no-private", "--no-git-hooks"]) == 0

    content = (repo / ".gitignore").read_bytes()
    assert content.startswith(b"node_modules/\r\n")
    assert b"\n" not in content.replace(b"\r\n", b"")
