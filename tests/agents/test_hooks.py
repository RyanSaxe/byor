"""AI agent adapters and the `byor hook` / `byor install` commands (global).

Hook registration is machine-wide, not per repo, so these tests drive the real command flow against
an isolated home: merging into existing Claude settings without clobbering them, refreshing outdated
hook commands, and uninstalling only the byor settings group. Doctor coverage lives here too,
flagging a recorded harness whose hook was removed or drifted.
"""

import json
from pathlib import Path

import pytest
from support import commands_in, global_agents, install_agents, make_repo

from byor.agents.hookconfig import BYOR_COMMAND_SIGNATURE
from byor.cli import main

SETTINGS_RELPATH = Path(".claude") / "settings.json"


def claude_settings(home: Path) -> Path:
    return home / SETTINGS_RELPATH


def claude_command(home: Path) -> str:
    commands = [
        command
        for command in commands_in(json.loads(claude_settings(home).read_text()))
        if BYOR_COMMAND_SIGNATURE in command
    ]
    [command] = commands
    return command


def test_install_writes_an_unguarded_global_hook(home: Path) -> None:
    install_agents("claude-code")

    command = claude_command(home)
    assert f"{BYOR_COMMAND_SIGNATURE} claude-code" in command
    # Global hooks are personal: no teammate guard.
    assert "command -v byor" not in command
    # The skill render lands globally too.
    assert (home / ".claude" / "skills" / "byor" / "SKILL.md").is_file()


def test_install_merges_into_existing_global_settings(home: Path) -> None:
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": []}}))

    install_agents("claude-code")

    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"] == []
    assert BYOR_COMMAND_SIGNATURE in json.dumps(data["hooks"]["PostToolUse"])

    snapshot = settings.read_text()
    install_agents("claude-code")
    assert settings.read_text() == snapshot


def test_outdated_claude_settings_hook_is_updated(home: Path) -> None:
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    stale = {
        "matcher": "Write",
        "hooks": [{"type": "command", "command": "byor agent-check --stdin-hook x"}],
    }
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [stale]}}))

    install_agents("claude-code")

    assert f"{BYOR_COMMAND_SIGNATURE} claude-code" in claude_command(home)


def test_invalid_claude_settings_fail_cleanly(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json")

    assert main(["install", "--agents", "claude-code", "--non-interactive"]) == 1

    err = capsys.readouterr().err
    assert ".claude/settings.json is not valid JSON" in err
    assert "Traceback" not in err


def test_hook_install_is_global_and_records_the_agent(home: Path) -> None:
    assert main(["hook", "install", "--agent", "codex"]) == 0

    assert (home / ".codex" / "hooks.json").is_file()
    assert global_agents() == ["codex"]


@pytest.mark.usefixtures("home")
def test_codex_install_prints_the_trust_step(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["hook", "install", "--agent", "codex"]) == 0

    assert "/hooks" in capsys.readouterr().out


def test_doctor_flags_a_recorded_harness_whose_hook_was_removed(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    install_agents("codex")
    hook = home / ".codex" / "hooks.json"
    hook.unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    out = capsys.readouterr().out
    assert "FAIL  agent_files" in out
    assert "the codex hook is not installed" in out
    assert "run `byor install`" in out
    # Doctor is read-only: reporting the problem must not reinstall the hook.
    assert not hook.exists()


def test_doctor_flags_a_recorded_harness_with_a_stale_matcher(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    install_agents("codex")
    hook = home / ".codex" / "hooks.json"
    data = json.loads(hook.read_text())
    data["hooks"]["PostToolUse"][0]["matcher"] = "Edit|Write"
    stale = json.dumps(data)
    hook.write_text(stale)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    out = capsys.readouterr().out
    assert "FAIL  agent_files" in out
    assert "the codex hook is out of date" in out
    assert "run `byor install`" in out
    # Doctor is read-only: the stale hook stays exactly as it was.
    assert hook.read_text() == stale


def test_hook_uninstall_removes_the_hook_and_record(home: Path) -> None:
    main(["hook", "install", "--agent", "codex"])

    assert main(["hook", "uninstall", "--agent", "codex"]) == 0

    assert not (home / ".codex" / "hooks.json").exists()
    assert global_agents() == []
    # Idempotent: a second uninstall has nothing to do and still succeeds.
    assert main(["hook", "uninstall", "--agent", "codex"]) == 0


def test_hook_uninstall_claude_code_removes_only_the_byor_settings_group(
    home: Path,
) -> None:
    user_group = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [user_group]}}))
    main(["hook", "install", "--agent", "claude-code"])

    assert main(["hook", "uninstall", "--agent", "claude-code"]) == 0

    data = json.loads(settings.read_text())
    assert data["hooks"]["PostToolUse"] == [user_group]
    assert BYOR_COMMAND_SIGNATURE not in settings.read_text()
