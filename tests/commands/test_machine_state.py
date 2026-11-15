"""Exercise byor's machine state: what it records about one computer, kept out of config.yml.

config.yml holds only the user's settings, so byor writes agents, disabled paths and the repo
registry to its state directory instead and never touches config.yml. A computer set up by an older
byor gets those three copied over on the first run, its old copies left in place, and doctor names
the leftovers so the user can delete them.
"""

from pathlib import Path

import pytest
from support import make_repo

from byor.cli import main
from byor.config import (
    GlobalConfig,
    InitDefaults,
    MachineState,
    load_machine_state,
    load_repo_registry,
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


def test_any_byor_command_records_an_agent_whose_byor_hooks_are_installed(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    assert main(["install", "--agents", "claude-code", "--non-interactive"]) == 0
    state = load_machine_state(global_state_dir())
    state.agents.remove("claude-code")
    save_machine_state(global_state_dir(), state)
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    assert "claude-code" in load_machine_state(global_state_dir()).agents
    assert "byor: recorded claude-code, whose byor integration was already installed" in capsys.readouterr().err
