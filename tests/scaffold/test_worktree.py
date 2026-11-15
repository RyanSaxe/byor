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
from support import commit_file, git, install_package, write_global_command_rule, write_global_rule, write_rule

from byor.cli import main
from byor.commands.sync import STALE_EXIT_CODE
from byor.config import GlobalConfig, save_global_config
from byor.io.paths import global_config_dir, global_state_dir
from byor.scaffold import worktree as worktree_copy

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


def add_worktree(main_repo: Path, *start: str) -> Path:
    worktree = main_repo.parent / "worktree"
    git(main_repo, "worktree", "add", "--quiet", str(worktree), *start)
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


def test_a_worktree_on_a_branch_from_before_byor_gets_nothing_it_could_commit(home: Path) -> None:
    worktree = add_worktree(make_main_checkout(home, private=False), "-b", "old", "HEAD~1")

    main(["list", "--repo", str(worktree)])

    assert not (worktree / ".byor").exists()
    assert git(worktree, "status", "--porcelain") == ""


def test_byor_init_as_the_first_command_in_a_new_worktree_keeps_the_main_checkouts_files(home: Path) -> None:
    main_repo = make_main_checkout(home, private=True)
    write_rule(main_repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    worktree = add_worktree(main_repo)

    assert main(["init", "--repo", str(worktree), "--non-interactive", "--private", "--no-git-hooks"]) == 0

    assert (worktree / ".byor" / "rules" / "project" / "no-cast.yml").is_file()
    assert str(worktree) not in (global_state_dir() / "repos.yml").read_text()


def test_doctor_and_sync_check_in_a_new_worktree_copy_nothing_and_point_to_byor_sync(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worktree = add_worktree(make_main_checkout(home, private=True))
    capsys.readouterr()

    assert main(["doctor", "--repo", str(worktree), "--quick"]) == 1
    assert main(["sync", "--check", "--repo", str(worktree)]) == STALE_EXIT_CODE

    out = capsys.readouterr().out
    assert "this worktree has not copied byor's files from its main checkout" in out
    assert "run `byor sync` to copy byor's files from the main checkout" in out
    assert not (worktree / ".byor").exists()


def test_a_worktree_of_a_disabled_main_checkout_gets_nothing_and_doctor_stays_clean(home: Path) -> None:
    main_repo = make_main_checkout(home, private=True)
    worktree = add_worktree(main_repo)
    assert main(["disable", str(main_repo)]) == 0

    main(["list", "--repo", str(worktree)])

    assert not (worktree / ".byor").exists()
    assert main(["doctor", "--repo", str(worktree), "--quick"]) == 0
    assert main(["sync", "--check", "--repo", str(worktree)]) == 0


def test_sync_all_reaches_the_worktrees_of_a_registered_repo(home: Path) -> None:
    worktree = add_worktree(make_main_checkout(home, private=False))
    main(["list", "--repo", str(worktree)])
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")

    assert main(["sync", "--all"]) == 0

    assert (worktree / ".byor" / "rules" / "personal" / "global" / "no-cast.yml").is_file()


def test_a_command_stops_while_another_process_holds_the_copy_lock(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    worktree = add_worktree(make_main_checkout(home, private=True))
    monkeypatch.setattr(worktree_copy, "LOCK_WAIT_SECONDS", 0.1)
    git_dir = Path(git(worktree, "rev-parse", "--absolute-git-dir").strip())
    (git_dir / worktree_copy.LOCK_NAME).touch()
    capsys.readouterr()

    assert main(["init", "--repo", str(worktree), "--non-interactive", "--private", "--no-git-hooks"]) != 0

    assert "another byor process is copying" in capsys.readouterr().err
    assert not (worktree / ".byor" / "local.yml").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs extra rights on Windows")
def test_a_linked_folder_of_rules_is_copied_as_the_same_link(home: Path) -> None:
    main_repo = make_main_checkout(home, private=True)
    shared = home / "shared"
    write_rule(shared / "no-cast.yml", "no-cast")
    (main_repo / ".byor" / "rules" / "personal" / "local" / "shared").symlink_to(shared)
    worktree = add_worktree(main_repo)

    assert main(["list", "--repo", str(worktree)]) == 0

    link = worktree / ".byor" / "rules" / "personal" / "local" / "shared"
    assert link.is_symlink()
    assert link.resolve() == shared.resolve()


def test_the_copy_deletes_partial_files_an_interrupted_copy_left(home: Path) -> None:
    worktree = add_worktree(make_main_checkout(home, private=True))
    leftover = worktree / ".byor" / "rules" / "personal" / "local" / ".big.yml.byor-copy-123"
    leftover.parent.mkdir(parents=True)
    leftover.write_text("partial")

    main(["list", "--repo", str(worktree)])

    assert not leftover.exists()
    assert (worktree / ".byor" / "local.yml").is_file()


def test_doctor_in_a_worktree_reports_a_missing_package_its_own_local_config_names(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worktree = add_worktree(make_main_checkout(home, private=True))
    main(["list", "--repo", str(worktree)])
    install_package(worktree, "gone")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(worktree), "--quick"]) == 1

    assert "~/worktree (gone)" in capsys.readouterr().out


@pytest.mark.parametrize("copy_ignored", [False, True])
def test_copy_ignored_brings_other_ignored_files_into_a_new_worktree_only_when_set(
    home: Path, capsys: pytest.CaptureFixture[str], *, copy_ignored: bool
) -> None:
    save_global_config(global_config_dir(), GlobalConfig(worktrees_copy_ignored=copy_ignored))
    main_repo = make_main_checkout(home, private=False)
    commit_file(main_repo, ".gitignore", content=(main_repo / ".gitignore").read_text() + ".env\n")
    (main_repo / ".env").write_text("TOKEN=1\n")
    worktree = add_worktree(main_repo)

    main(["list", "--repo", str(worktree)])

    assert (worktree / ".env").exists() is copy_ignored
    assert ("byor file and 1 other ignored file from the main checkout" in capsys.readouterr().err) is copy_ignored


def test_commands_skip_the_other_ignored_files_while_another_process_copies_them(home: Path) -> None:
    main_repo = make_main_checkout(home, private=False)
    commit_file(main_repo, ".gitignore", content=(main_repo / ".gitignore").read_text() + ".env\n")
    (main_repo / ".env").write_text("TOKEN=1\n")
    worktree = add_worktree(main_repo)
    main(["list", "--repo", str(worktree)])
    save_global_config(global_config_dir(), GlobalConfig(worktrees_copy_ignored=True))
    lock = Path(git(worktree, "rev-parse", "--absolute-git-dir").strip()) / worktree_copy.LOCK_NAME
    lock.touch()

    assert main(["list", "--repo", str(worktree)]) == 0
    assert not (worktree / ".env").exists()

    lock.unlink()
    main(["list", "--repo", str(worktree)])
    assert (worktree / ".env").read_text() == "TOKEN=1\n"
