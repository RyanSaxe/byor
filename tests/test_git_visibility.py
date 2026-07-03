"""Personal rules in a real git repository, against the real ast-grep binary.

ast-grep's rule discovery respects gitignore, so the git-ignored personal rule copies need the
`.ignore` negation files to stay loadable while staying invisible to `git status`. These tests prove
both halves with real git and real ast-grep: scans see synced global, package, and local rules, and
sync restores a deleted visibility file.
"""

import subprocess
from pathlib import Path

import pytest
from support import (
    git,
    install_package,
    mirror,
    write_global_rule,
    write_package_rule,
    write_rule,
)

from byor.cli import main
from byor.scan.astgrep import resolve_ast_grep

VIOLATION = 'from typing import cast\nx = cast(int, "5")\n'


def git_repo(home: Path, *init_extra: str) -> Path:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", *init_extra]) == 0
    return repo


def scan(repo: Path) -> str:
    # resolve_ast_grep applies PATHEXT, so it finds ast-grep.cmd on Windows
    # where a bare "ast-grep" argv entry would not.
    result = subprocess.run(
        [str(resolve_ast_grep()), "scan"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result.stdout


@pytest.mark.parametrize("private", [False, True])
def test_synced_global_rule_is_seen_by_ast_grep_scan(home: Path, *, private: bool) -> None:
    write_global_rule(home, "python/no-python-cast.yml", rule_id="no-python-cast")
    repo = git_repo(home, *(["--private"] if private else []))
    (repo / "app.py").write_text(VIOLATION)

    assert "no-python-cast" in scan(repo)


def test_installed_package_rule_is_seen_by_ast_grep_and_hidden_from_git(
    home: Path,
) -> None:
    write_package_rule(home, "python-strict", relpath="no-python-cast.yml", rule_id="no-python-cast")
    repo = git_repo(home)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "init")
    install_package(repo, "python-strict")
    assert main(["sync", "--repo", str(repo)]) == 0
    (repo / "app.py").write_text(VIOLATION)

    assert "no-python-cast" in scan(repo)
    assert "personal/packages" not in git(repo, "status", "--porcelain")


def test_local_personal_rule_is_seen_by_ast_grep_scan(home: Path) -> None:
    repo = git_repo(home)
    local_rules = repo / ".byor" / "rules" / "personal" / "local"
    write_rule(local_rules / "my-local-rule.yml", "my-local-rule")
    (repo / "app.py").write_text(VIOLATION)

    assert "my-local-rule" in scan(repo)


def test_synced_nested_copy_is_invisible_to_git_status(home: Path) -> None:
    repo = git_repo(home)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "init")
    write_global_rule(home, "python/nested-rule.yml", rule_id="nested-rule")

    assert main(["sync", "--repo", str(repo)]) == 0

    assert (mirror(repo) / "python" / "nested-rule.yml").is_file()
    assert git(repo, "status", "--porcelain") == ""


def test_agent_check_reports_violations_of_synced_global_rules(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_global_rule(home, "python/no-python-cast.yml", rule_id="no-python-cast")
    repo = git_repo(home)
    source = repo / "app.py"
    source.write_text(VIOLATION)
    capsys.readouterr()

    assert main(["agent-check", "--repo", str(repo), "--files", str(source)]) == 2

    assert "no-python-cast" in capsys.readouterr().out


def test_sync_restores_a_deleted_visibility_file_in_the_mirror(home: Path) -> None:
    write_global_rule(home, "python/no-python-cast.yml", rule_id="no-python-cast")
    repo = git_repo(home)
    (mirror(repo) / ".ignore").unlink()
    (repo / "app.py").write_text(VIOLATION)
    assert "no-python-cast" not in scan(repo)

    assert main(["list", "--repo", str(repo)]) == 0  # any command self-heals

    assert "no-python-cast" in scan(repo)
