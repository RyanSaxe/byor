"""AI agent adapters and the `byor hook` command (global registration)."""

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


def claude_settings(home: Path) -> Path:
    """The global claude settings the hook lands in (home is sandboxed in tests)."""
    return home / SETTINGS_RELPATH


def claude_command(home: Path) -> str:
    """The single PostToolUse command byor installed in the global claude settings."""
    commands = [
        command
        for command in commands_in(json.loads(claude_settings(home).read_text()))
        if BYOR_COMMAND_SIGNATURE in command
    ]
    [command] = commands
    return command


def test_claude_code_install_writes_an_unguarded_global_hook(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "claude-code")

    command = claude_command(home)
    assert f"{BYOR_COMMAND_SIGNATURE} claude-code" in command
    # Global hooks are personal: no teammate guard.
    assert "command -v byor" not in command
    # The skill render lands globally too, never in the repo.
    assert (home / ".claude" / "skills" / "byor" / "SKILL.md").is_file()
    assert not (repo / ".claude" / "skills").exists()


def test_claude_code_install_merges_into_existing_global_settings(home: Path) -> None:
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": []}}))
    repo = home / "repo"

    assert init_with_agents(repo, "claude-code") == 0

    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"] == []
    assert BYOR_COMMAND_SIGNATURE in json.dumps(data["hooks"]["PostToolUse"])

    snapshot = settings.read_text()
    assert init_with_agents(repo, "claude-code") == 0
    assert settings.read_text() == snapshot

    # The global hook plus the repo skill render satisfy doctor's agent_files check.
    assert main(["doctor", "--repo", str(repo), "--quick"]) == 0


def test_outdated_claude_settings_hook_is_updated(home: Path) -> None:
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    stale = {
        "matcher": "Write",
        "hooks": [{"type": "command", "command": "byor agent-check --stdin-hook x"}],
    }
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [stale]}}))
    repo = home / "repo"

    assert init_with_agents(repo, "claude-code") == 0

    assert f"{BYOR_COMMAND_SIGNATURE} claude-code" in claude_command(home)


def test_invalid_claude_settings_fail_cleanly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json")
    repo = home / "repo"

    assert init_with_agents(repo, "claude-code") == 1

    err = capsys.readouterr().err
    assert ".claude/settings.json is not valid JSON" in err
    assert "Traceback" not in err


def hook(action: str, repo: Path, agent: str, *extra: str) -> int:
    return main(["hook", action, "--repo", str(repo), "--agent", agent, *extra])


def test_hook_install_writes_the_global_adapter_and_records_the_agent(
    home: Path,
) -> None:
    repo = make_repo(home)

    assert hook("install", repo, "codex") == 0

    assert (home / ".codex" / "hooks.json").is_file()
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

    hooks = json.loads((home / ".cursor" / "hooks.json").read_text())
    assert BYOR_COMMAND_SIGNATURE in json.dumps(hooks)
    assert load_repo_config(repo).agents == ["skill", "cursor"]


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
    (home / ".codex" / "hooks.json").unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    assert "the codex hook is not installed" in capsys.readouterr().out


def test_hook_uninstall_removes_the_installed_adapter_and_hook(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "codex")

    assert hook("uninstall", repo, "codex") == 0

    assert not (home / ".codex" / "hooks.json").exists()
    assert load_repo_config(repo).agents == ["skill"]
    # Idempotent: a second uninstall has nothing to do and still succeeds.
    assert hook("uninstall", repo, "codex") == 0


def test_hook_uninstall_claude_code_removes_only_the_byor_settings_group(
    home: Path,
) -> None:
    repo = make_repo(home)
    user_group = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [user_group]}}))
    hook("install", repo, "claude-code")

    assert hook("uninstall", repo, "claude-code") == 0

    data = json.loads(settings.read_text())
    assert data["hooks"]["PostToolUse"] == [user_group]
    assert BYOR_COMMAND_SIGNATURE not in settings.read_text()
