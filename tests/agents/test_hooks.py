"""AI agent adapters and the `byor hook` command."""

import json
from pathlib import Path

import pytest
from support import commands_in, make_repo

from byor.agents.hookconfig import BYOR_COMMAND_SIGNATURE
from byor.cli import main
from byor.config import load_repo_config

SETTINGS_RELPATH = Path(".claude") / "settings.json"


def init_with_agents(repo: Path, agents: str) -> int:
    return main(["init", "--repo", str(repo), "--non-interactive", "--agents", agents])


def claude_command(repo: Path) -> str:
    """The single PostToolUse command byor installed in claude settings."""
    text = (repo / SETTINGS_RELPATH).read_text()
    commands = [
        command
        for command in commands_in(json.loads(text))
        if BYOR_COMMAND_SIGNATURE in command
    ]
    [command] = commands
    return command


def test_claude_code_install_writes_a_guarded_project_hook(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "claude-code")

    command = claude_command(repo)
    assert f"{BYOR_COMMAND_SIGNATURE} claude-code" in command
    assert "command -v byor" in command
    # The skill render still creates .claude/skills/ alongside the hook.
    assert (repo / ".claude" / "skills" / "byor" / "SKILL.md").is_file()


def test_claude_code_install_merges_into_existing_settings(home: Path) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)
    settings = repo / SETTINGS_RELPATH
    settings.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": []}}))

    assert init_with_agents(repo, "claude-code") == 0

    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"] == []
    assert BYOR_COMMAND_SIGNATURE in json.dumps(data["hooks"]["PostToolUse"])

    snapshot = settings.read_text()
    assert init_with_agents(repo, "claude-code") == 0
    assert settings.read_text() == snapshot

    # The settings hook plus the skill render satisfy doctor's agent_files check.
    assert main(["doctor", "--repo", str(repo), "--quick"]) == 0


def test_outdated_claude_settings_hook_is_updated(home: Path) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)
    settings = repo / SETTINGS_RELPATH
    stale = {
        "matcher": "Write",
        "hooks": [{"type": "command", "command": "byor agent-check --stdin-hook x"}],
    }
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [stale]}}))

    assert init_with_agents(repo, "claude-code") == 0

    assert f"{BYOR_COMMAND_SIGNATURE} claude-code" in claude_command(repo)


def test_invalid_claude_settings_fail_cleanly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / SETTINGS_RELPATH).write_text("{not json")

    assert init_with_agents(repo, "claude-code") == 1

    err = capsys.readouterr().err
    assert ".claude/settings.json is not valid JSON" in err
    assert "Traceback" not in err


def hook(action: str, repo: Path, agent: str, *extra: str) -> int:
    return main(["hook", action, "--repo", str(repo), "--agent", agent, *extra])


def test_hook_install_writes_the_adapter_and_records_the_agent(home: Path) -> None:
    repo = make_repo(home)

    assert hook("install", repo, "codex") == 0

    assert (repo / ".codex" / "hooks.json").is_file()
    assert load_repo_config(repo).agents == ["skill", "codex"]


def test_codex_install_prints_the_trust_step(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)

    assert hook("install", repo, "codex") == 0

    assert "/hooks" in capsys.readouterr().out


def test_cursor_is_a_full_agent_choice(home: Path) -> None:
    repo = make_repo(home)

    assert hook("install", repo, "cursor") == 0

    hooks = json.loads((repo / ".cursor" / "hooks.json").read_text())
    assert BYOR_COMMAND_SIGNATURE in json.dumps(hooks)
    assert load_repo_config(repo).agents == ["skill", "cursor"]


def test_hook_install_global_scope_writes_under_home(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = home / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    repo = make_repo(home)

    assert hook("install", repo, "cursor", "--hook-scope", "global") == 0

    assert (fake_home / ".cursor" / "hooks.json").is_file()
    assert not (repo / ".cursor" / "hooks.json").exists()


def test_hook_install_local_scope_is_claude_code_only(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)

    assert hook("install", repo, "codex", "--hook-scope", "local") == 1

    assert "only supported for claude-code" in capsys.readouterr().err


def test_hook_install_requires_an_initialized_repo(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "bare"
    repo.mkdir()

    assert hook("install", repo, "codex") == 1

    err = capsys.readouterr().err
    assert "byor init" in err
    assert "Traceback" not in err


def test_doctor_flags_a_recorded_harness_whose_hook_was_removed(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home, "repo", "--agents", "codex")
    (repo / ".codex" / "hooks.json").unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    assert "the codex hook is not installed" in capsys.readouterr().out


def test_hook_uninstall_removes_the_installed_adapter_and_hook(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "codex")

    assert hook("uninstall", repo, "codex") == 0

    assert not (repo / ".codex" / "hooks.json").exists()
    assert load_repo_config(repo).agents == ["skill"]
    # Idempotent: a second uninstall has nothing to do and still succeeds.
    assert hook("uninstall", repo, "codex") == 0


def test_hook_uninstall_claude_code_removes_only_the_byor_settings_group(
    home: Path,
) -> None:
    repo = make_repo(home)
    user_group = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
    (repo / ".claude").mkdir(exist_ok=True)
    settings = repo / SETTINGS_RELPATH
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [user_group]}}))
    hook("install", repo, "claude-code")

    assert hook("uninstall", repo, "claude-code") == 0

    data = json.loads(settings.read_text())
    assert data["hooks"]["PostToolUse"] == [user_group]
    assert BYOR_COMMAND_SIGNATURE not in settings.read_text()
