"""Exercise byor's machine state: what it records about one computer, kept out of config.yml.

config.yml holds only the user's settings, so byor writes agents, disabled paths and the repo
registry to its state directory instead and never touches config.yml. A computer set up by an older
byor gets those three copied over on the first run, its old copies left in place, and doctor names
the leftovers so the user can delete them.
"""

import io
import json
import os
import sys
from pathlib import Path

import pytest
from support import make_repo, write_command_rule

from byor.cli import main
from byor.config import (
    GlobalConfig,
    InitDefaults,
    MachineState,
    load_machine_state,
    load_repo_registry,
    machine_state_path,
    save_global_config,
    save_machine_state,
)
from byor.io.paths import global_config_dir, global_state_dir


# Write config.yml and repos.yml the way byor kept them before it had a state directory.
def write_old_layout(home: Path) -> None:
    config_dir = global_config_dir()
    config_dir.mkdir(parents=True)
    (config_dir / "config.yml").write_text(
        f"version: 1\nai:\n  agents:\n    - codex\n    - skill\ndisabled_repos:\n  - {home / 'legacy'}\n"
        "init:\n  private: true\n"
    )
    (config_dir / "repos.yml").write_text(f"version: 1\nrepos:\n  - {home / 'repo'}\n")


@pytest.mark.usefixtures("home")
def test_install_records_agents_in_state_and_leaves_the_users_config_alone() -> None:
    save_global_config(global_config_dir(), GlobalConfig(init=InitDefaults(private=True)))
    config_file = global_config_dir() / "config.yml"
    before = config_file.read_bytes()

    assert main(["install", "--non-interactive", "--agents", "claude-code"]) == 0

    assert config_file.read_bytes() == before
    assert load_machine_state(global_state_dir()).agents == ["claude-code", "skill"]


@pytest.mark.usefixtures("home")
def test_install_without_a_config_never_creates_one() -> None:
    assert main(["install", "--non-interactive", "--agents", "codex"]) == 0

    assert not (global_config_dir() / "config.yml").exists()


def test_first_run_copies_the_old_machine_state_and_leaves_the_old_files(home: Path) -> None:
    write_old_layout(home)
    old_config = (global_config_dir() / "config.yml").read_bytes()
    old_registry = (global_config_dir() / "repos.yml").read_bytes()

    main(["doctor", "--quick"])

    assert load_machine_state(global_state_dir()) == MachineState(
        agents=["codex", "skill"], disabled_repos=[home / "legacy"]
    )
    assert load_repo_registry(global_state_dir() / "repos.yml") == [home / "repo"]
    assert (global_config_dir() / "config.yml").read_bytes() == old_config
    assert (global_config_dir() / "repos.yml").read_bytes() == old_registry


def test_the_copy_happens_once(home: Path) -> None:
    write_old_layout(home)
    main(["doctor", "--quick"])
    (global_config_dir() / "config.yml").write_text("version: 1\nai:\n  agents:\n    - copilot\n")

    main(["doctor", "--quick"])

    assert load_machine_state(global_state_dir()).agents == ["codex", "skill"]


def test_doctor_names_old_machine_state_left_under_the_config_dir(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_old_layout(home)
    capsys.readouterr()

    assert main(["doctor", "--quick"]) == 1

    out = capsys.readouterr().out
    assert "FAIL  legacy_state" in out
    assert "byor no longer reads these entries" in out
    assert "now: `ai.agents` in config.yml, `disabled_repos` in config.yml, repos.yml; delete them" in out


@pytest.mark.usefixtures("home")
def test_doctor_has_no_leftover_row_once_the_old_entries_are_gone(capsys: pytest.CaptureFixture[str]) -> None:
    save_global_config(global_config_dir(), GlobalConfig(init=InitDefaults(private=True)))
    main(["install", "--non-interactive", "--agents", "skill"])
    capsys.readouterr()

    main(["doctor", "--quick"])

    assert "legacy_state" not in capsys.readouterr().out


def test_an_absolute_registry_path_is_live_not_a_leftover(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = home / "elsewhere" / "repos.yml"
    save_global_config(global_config_dir(), GlobalConfig(repos_path=str(registry)))
    assert main(["init", "--repo", str(home), "--non-interactive"]) == 0
    capsys.readouterr()

    main(["doctor", "--quick"])

    assert load_repo_registry(registry) == [home.resolve()]
    assert "legacy_state" not in capsys.readouterr().out


# Install agents, then drop them from state.yml so their integrations are installed but unrecorded.
def install_unrecorded(*agents: str) -> None:
    assert main(["install", "--agents", ",".join(agents), "--non-interactive"]) == 0
    state = load_machine_state(global_state_dir())
    state.agents = [agent for agent in state.agents if agent not in agents]
    save_machine_state(global_state_dir(), state)


def recorded_agents() -> list[str]:
    return load_machine_state(global_state_dir()).agents


def test_any_byor_command_records_an_agent_whose_byor_hooks_are_installed(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    install_unrecorded("claude-code")
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    assert "claude-code" in recorded_agents()
    assert "byor: recorded claude-code, whose byor integration was already installed" in capsys.readouterr().err


def test_an_unreadable_agent_config_is_a_warning_and_other_agents_are_still_recorded(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    install_unrecorded("codex", "opencode")
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / "settings.json").write_bytes(b'{"theme": "caf\xe9"}')
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    assert {"codex", "opencode"} <= set(recorded_agents())
    assert "~/.claude/settings.json is not valid UTF-8" in capsys.readouterr().err


def test_hook_uninstall_lasts_when_a_user_command_shares_byors_hook_entry(home: Path) -> None:
    repo = make_repo(home)
    assert main(["install", "--agents", "claude-code", "--non-interactive"]) == 0
    settings_path = home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["PostToolUse"][0]["hooks"].append({"type": "command", "command": "my-formatter"})
    settings_path.write_text(json.dumps(settings))

    assert main(["hook", "uninstall", "--agent", "claude-code"]) == 0
    assert main(["list", "--repo", str(repo)]) == 0

    assert "claude-code" not in recorded_agents()
    assert "PreToolUse" not in json.loads(settings_path.read_text())["hooks"]


@pytest.mark.usefixtures("home")
def test_uninstalling_an_unrecorded_agent_does_not_record_it_first(capsys: pytest.CaptureFixture[str]) -> None:
    install_unrecorded("codex")
    capsys.readouterr()

    assert main(["hook", "uninstall", "--agent", "codex"]) == 0

    assert "recorded" not in capsys.readouterr().err
    assert "codex" not in recorded_agents()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as a non-root user")
def test_self_heal_warns_instead_of_failing_when_it_cannot_write_state(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    install_unrecorded("claude-code")
    state_dir = global_state_dir()
    state_dir.chmod(0o500)
    capsys.readouterr()
    try:
        assert main(["list", "--repo", str(repo)]) == 0
    finally:
        state_dir.chmod(0o700)

    assert "byor: skipping agent recording: cannot write byor's state" in capsys.readouterr().err


def test_doctor_names_an_old_registry_by_its_configured_path(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_dir = global_config_dir()
    (config_dir / "registry").mkdir(parents=True)
    (config_dir / "config.yml").write_text("paths:\n  repos: registry/my-repos.yml\n")
    (config_dir / "registry" / "my-repos.yml").write_text(f"version: 1\nrepos:\n  - {home / 'repo'}\n")
    capsys.readouterr()

    main(["doctor", "--quick"])

    assert "now: registry/my-repos.yml; delete them" in capsys.readouterr().out


@pytest.mark.parametrize("hook", ["agent-check", "command-check"])
def test_a_hook_fails_open_when_the_state_move_fails(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
    hook: str,
) -> None:
    global_config_dir().mkdir(parents=True)
    (global_config_dir() / "config.yml").write_text("version: 2\n")
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"tool_input": {"command": "ls", "file_path": str(home / "a.py")}}))
    )
    capsys.readouterr()

    assert main([hook, "--stdin-hook", "claude-code"]) == 0

    assert "byor: skipping the state move" in capsys.readouterr().err
    assert main(["doctor", "--quick"]) != 0


def test_a_deleted_state_file_gets_the_skill_recorded_again(home: Path) -> None:
    repo = make_repo(home)
    assert main(["install", "--agents", "skill", "--non-interactive"]) == 0
    machine_state_path(global_state_dir()).unlink()

    assert main(["list", "--repo", str(repo)]) == 0

    assert "skill" in recorded_agents()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as a non-root user")
def test_disable_reports_an_unwritable_state_folder_as_an_error(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    save_machine_state(global_state_dir(), MachineState())
    target = home / "some-repo"
    target.mkdir()
    global_state_dir().chmod(0o500)
    capsys.readouterr()
    try:
        assert main(["disable", str(target)]) != 0
    finally:
        global_state_dir().chmod(0o700)

    err = capsys.readouterr().err
    assert "cannot write byor's state to" in err
    assert "Traceback" not in err


def test_a_state_file_lost_after_the_move_starts_empty(home: Path) -> None:
    write_old_layout(home)
    main(["doctor", "--quick"])
    machine_state_path(global_state_dir()).unlink()

    main(["doctor", "--quick"])

    assert load_machine_state(global_state_dir()) == MachineState()


def test_an_undecodable_old_registry_is_named_and_hooks_carry_on(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_old_layout(home)
    (global_config_dir() / "repos.yml").write_bytes(b"version: 1\nrepos:\n  - /tmp/caf\xe9\n")
    capsys.readouterr()

    assert main(["doctor", "--quick"]) != 0
    assert "repos.yml: not valid UTF-8" in capsys.readouterr().err

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"command": "ls"}, "cwd": str(home)})))
    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0
    assert "byor: skipping the state move" in capsys.readouterr().err


def test_a_hook_honors_old_disabled_paths_when_the_move_fails(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    write_command_rule(repo / ".byor" / "commands" / "project" / "no-pip.yml", "no-pip")
    (global_config_dir() / "config.yml").write_text(f"version: 1\ndisabled_repos:\n  - {repo}\n")
    unwritable = home / "not-a-directory"
    unwritable.write_text("")
    monkeypatch.setenv("XDG_STATE_HOME", str(unwritable))
    payload = {"tool_input": {"command": "pip install requests"}, "cwd": str(repo)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    captured = capsys.readouterr()
    assert "byor: skipping the state move" in captured.err
    assert captured.out == ""


def test_a_duplicate_or_unsupported_agent_in_state_does_not_survive(home: Path) -> None:
    repo = make_repo(home)
    assert main(["install", "--agents", "claude-code", "--non-interactive"]) == 0
    machine_state_path(global_state_dir()).write_text(
        "version: 1\nagents:\n  - claude-code\n  - claude-code\n  - cursor\n  - skill\ndisabled_repos: []\n"
    )

    assert main(["hook", "uninstall", "--agent", "claude-code"]) == 0
    assert main(["list", "--repo", str(repo)]) == 0

    assert recorded_agents() == ["skill"]
