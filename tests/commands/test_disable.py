"""Exercise `byor disable` and `byor enable`.

Disable entries live only in the global config — resolved repo roots or directory prefixes — and
never write into the repo, so they work for repos that never ran `byor init`. A covered repo
silences the post-edit hook, turns explicit commands into a one-line stderr notice, is skipped by
`sync --all`, and makes `byor init` ask before lifting an exact entry (never a prefix).
"""

import io
import json
import sys
from pathlib import Path

import pytest
from support import git, make_repo, mirror, write_global_rule

from byor.cli import main
from byor.config import load_global_config
from byor.io.paths import global_config_dir
from byor.scaffold.sgconfig import ensure_home_sgconfig


def setup_global_scan(home: Path) -> None:
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    ensure_home_sgconfig(home / "xdg" / "byor" / "rules", home=home)


def git_repo_with_violation(home: Path, name: str) -> Path:
    repo = home / name
    repo.mkdir(parents=True)
    git(repo, "init", "--quiet")
    (repo / "src.py").write_text('x = cast(int, "1")\n')
    return repo


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def hook_edit(monkeypatch: pytest.MonkeyPatch, source: Path) -> int:
    payload = json.dumps({"tool_input": {"file_path": str(source)}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    return main(["agent-check", "--stdin-hook", "claude-code"])


def disabled_repos() -> list[Path]:
    return load_global_config(global_config_dir()).disabled_repos


def test_disable_silences_the_hook_in_an_uninitd_repo_but_not_siblings(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_global_scan(home)
    legacy = git_repo_with_violation(home, "legacy")
    active = git_repo_with_violation(home, "active")

    assert main(["disable", str(legacy)]) == 0
    capsys.readouterr()

    assert hook_edit(monkeypatch, legacy / "src.py") == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    assert hook_edit(monkeypatch, active / "src.py") == 2
    assert "no-cast" in capsys.readouterr().out


def test_a_prefix_entry_covers_child_repos(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_global_scan(home)
    child = git_repo_with_violation(home, "clients/repo-a")

    assert main(["disable", str(home / "clients")]) == 0
    capsys.readouterr()

    assert hook_edit(monkeypatch, child / "src.py") == 0
    assert capsys.readouterr().out == ""

    monkeypatch.chdir(child)
    assert main(["list"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "this repository is disabled for byor; run `byor enable` to re-enable" in captured.err


def test_prefix_matching_is_path_aware_not_string_prefix(home: Path) -> None:
    (home / "cli").mkdir()
    clients = home / "clients"
    clients.mkdir()

    assert main(["disable", str(home / "cli")]) == 0
    assert main(["disable", str(clients)]) == 0

    assert disabled_repos() == [(home / "cli").resolve(), clients.resolve()]


def test_disable_defaults_to_the_enclosing_repo_root(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = home / "legacy"
    (repo / "sub").mkdir(parents=True)
    git(repo, "init", "--quiet")
    monkeypatch.chdir(repo / "sub")

    assert main(["disable"]) == 0

    assert disabled_repos() == [repo.resolve()]


def test_disable_without_git_disables_the_cwd_itself(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = home / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)

    assert main(["disable"]) == 0

    assert disabled_repos() == [plain.resolve()]


def test_disable_dedupes_and_reports_an_existing_covering_entry(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clients = home / "clients"
    child = clients / "repo-a"
    child.mkdir(parents=True)
    main(["disable", str(clients)])
    capsys.readouterr()

    assert main(["disable", str(clients)]) == 0
    assert "is already disabled" in capsys.readouterr().out

    assert main(["disable", str(child)]) == 0
    assert f"already disabled by {clients.resolve()}" in capsys.readouterr().out

    assert disabled_repos() == [clients.resolve()]


def test_enable_removes_an_exact_entry_and_drops_the_empty_key(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "legacy"
    repo.mkdir()
    main(["disable", str(repo)])
    capsys.readouterr()

    assert main(["enable", str(repo)]) == 0

    assert f"Enabled byor in {repo.resolve()}" in capsys.readouterr().out
    assert disabled_repos() == []
    assert "disabled_repos" not in (global_config_dir() / "config.yml").read_text()


def test_enable_under_a_prefix_names_the_covering_entry(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clients = home / "clients"
    child = clients / "repo-a"
    child.mkdir(parents=True)
    main(["disable", str(clients)])
    capsys.readouterr()

    assert main(["enable", str(child)]) == 0

    assert f"run `byor enable {clients.resolve()}` to lift it" in capsys.readouterr().out
    assert disabled_repos() == [clients.resolve()]

    assert main(["enable", str(home / "elsewhere")]) == 0
    assert "is not disabled" in capsys.readouterr().out


def test_commands_in_a_disabled_repo_print_one_notice_and_exit_zero(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    main(["disable", str(repo)])
    capsys.readouterr()

    for argv in (["sync", "--repo", str(repo)], ["list", "--repo", str(repo)]):
        assert main(argv) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "this repository is disabled for byor; run `byor enable` to re-enable" in captured.err


def test_sync_all_skips_disabled_repos_with_a_note(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    active = make_repo(home, name="active")
    legacy = make_repo(home, name="legacy")
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    main(["disable", str(legacy)])
    capsys.readouterr()

    assert main(["sync", "--all"]) == 0

    captured = capsys.readouterr()
    assert (mirror(active) / "no-cast.yml").is_file()
    assert not (mirror(legacy) / "no-cast.yml").exists()
    assert f"byor: skipping {legacy.resolve()}: disabled for byor" in captured.err


def test_init_interactive_yes_enables_and_continues(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = home / "legacy"
    repo.mkdir()
    main(["disable", str(repo)])
    capsys.readouterr()
    # Answers: enable -> yes; private, git hooks, and gate keep their no defaults.
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n\n\n\n"))

    assert main(["init", "--repo", str(repo)]) == 0

    assert disabled_repos() == []
    assert (repo / ".byor" / "config.yml").is_file()
    assert f"Enabled byor in {repo}" in capsys.readouterr().out


def test_init_interactive_no_keeps_the_repo_disabled(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = home / "legacy"
    repo.mkdir()
    main(["disable", str(repo)])
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))

    assert main(["init", "--repo", str(repo)]) == 1

    assert "byor enable" in capsys.readouterr().err
    assert disabled_repos() == [repo.resolve()]
    assert not (repo / ".byor").exists()


def test_init_non_interactive_aborts_in_a_disabled_repo(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "legacy"
    repo.mkdir()
    main(["disable", str(repo)])
    capsys.readouterr()

    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 1

    assert f"run `byor enable {repo}` first" in capsys.readouterr().err
    assert not (repo / ".byor").exists()


def test_init_under_a_prefix_entry_explains_instead_of_prompting(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clients = home / "clients"
    child = clients / "repo-a"
    child.mkdir(parents=True)
    main(["disable", str(clients)])
    capsys.readouterr()

    assert main(["init", "--repo", str(child)]) == 1

    assert f"run `byor enable {clients.resolve()}` to lift it" in capsys.readouterr().err
    assert disabled_repos() == [clients.resolve()]
    assert not (child / ".byor").exists()


def test_doctor_lists_disabled_paths_and_stays_silent_without_any(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0
    assert "disabled_paths" not in capsys.readouterr().out

    (home / "legacy").mkdir()
    (home / "clients").mkdir()
    main(["disable", str(home / "legacy")])
    main(["disable", str(home / "clients")])
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "ok    disabled_paths" in out
    assert "2 paths disabled:" in out
    assert "legacy" in out
    assert "clients" in out
