"""Exercise how a new linked worktree gets its main checkout's byor files.

git checks out only tracked files, so each test builds a real main checkout, runs `git worktree add`
from it, and drives byor in the worktree. The cases cover both kinds of init, both agent hooks, the
self-heal preamble, a worktree that already has its own files, and a repo byor never initialized.
"""

import io
import json
import sys
from pathlib import Path

import pytest
from support import commit_file, git, write_global_command_rule, write_rule

from byor.cli import main

PIP_COMMAND = "pip install requests"


def make_main_checkout(home: Path, *, private: bool) -> Path:
    repo = home / "main"
    repo.mkdir()
    git(repo, "init", "--quiet")
    commit_file(repo, "README.md", content="main\n")
    mode = "--private" if private else "--no-private"
    assert main(["init", "--repo", str(repo), "--non-interactive", mode, "--no-git-hooks"]) == 0
    if not private:
        git(repo, "add", "--all")
        git(repo, "commit", "--quiet", "-m", "add byor")
    return repo


def add_worktree(main_repo: Path) -> Path:
    worktree = main_repo.parent / "worktree"
    git(main_repo, "worktree", "add", "--quiet", str(worktree))
    return worktree


def test_the_edit_hook_in_a_new_worktree_of_a_private_init_applies_the_main_project_rules(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_repo = make_main_checkout(home, private=True)
    write_rule(main_repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    worktree = add_worktree(main_repo)
    source = worktree / "src.py"
    source.write_text('x = cast(int, "1")\n')
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"file_path": str(source)}})))
    capsys.readouterr()

    assert main(["agent-check", "--stdin-hook", "claude-code"]) == 2

    assert "Rule: no-cast" in capsys.readouterr().out
    assert (worktree / ".byor" / "local.yml").is_file()


def test_the_command_hook_in_a_new_worktree_of_a_committed_init_applies_global_command_rules(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_global_command_rule(home, "no-pip.yml", rule_id="no-pip")
    worktree = add_worktree(make_main_checkout(home, private=False))
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"tool_input": {"command": PIP_COMMAND}, "cwd": str(worktree)}))
    )
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    envelope = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert envelope["permissionDecision"] == "deny"
    assert "no-pip" in envelope["permissionDecisionReason"]


def test_a_byor_command_in_a_new_worktree_reports_the_copy(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    worktree = add_worktree(make_main_checkout(home, private=True))
    capsys.readouterr()

    assert main(["list", "--repo", str(worktree)]) == 0

    assert "from the main checkout" in capsys.readouterr().err
    assert (worktree / ".byor" / "config.yml").is_file()


def test_a_worktree_with_its_own_local_config_keeps_it(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    worktree = add_worktree(make_main_checkout(home, private=True))
    own_local = worktree / ".byor" / "local.yml"
    own_local.parent.mkdir()
    own_local.write_text("version: 1\npackages: []\n")
    capsys.readouterr()

    main(["list", "--repo", str(worktree)])

    assert own_local.read_text() == "version: 1\npackages: []\n"
    assert not (worktree / ".byor" / "config.yml").exists()


def test_a_worktree_of_a_repo_byor_never_initialized_gets_no_byor_files(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_repo = home / "main"
    main_repo.mkdir()
    git(main_repo, "init", "--quiet")
    commit_file(main_repo, "README.md", content="main\n")
    worktree = add_worktree(main_repo)
    source = worktree / "src.py"
    source.write_text("x = 1\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"file_path": str(source)}})))

    assert main(["agent-check", "--stdin-hook", "claude-code"]) == 0

    assert not (worktree / ".byor").exists()
