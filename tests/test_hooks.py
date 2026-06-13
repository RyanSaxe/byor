"""AI agent adapters and the `byolsp hook` command (SPEC 15.10, 16, 28.3)."""

import json
from pathlib import Path

import pytest
from conftest import make_repo

from byolsp.agents import MANAGED_MARKER
from byolsp.cli import main
from byolsp.config import load_repo_config
from byolsp.hookconfig import BYOLSP_COMMAND_SIGNATURE

AGENTS_DIR = Path(".byolsp") / "agents"

SETTINGS_RELPATH = Path(".claude") / "settings.json"


def init_with_agents(repo: Path, agents: str) -> int:
    return main(["init", "--repo", str(repo), "--non-interactive", "--agents", agents])


def claude_command(repo: Path) -> str:
    """The single PostToolUse command byolsp installed in claude settings."""
    text = (repo / SETTINGS_RELPATH).read_text()
    commands = [
        command
        for command in _all_commands(json.loads(text))
        if BYOLSP_COMMAND_SIGNATURE in command
    ]
    [command] = commands
    return command


def _all_commands(node: object) -> list[str]:
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            if key == "command" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_all_commands(value))
        return found
    if isinstance(node, list):
        return [command for item in node for command in _all_commands(item)]
    return []


def test_init_installs_instruction_files_for_requested_agents(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "codex,copilot")

    for name in ("codex.md", "copilot.md"):
        content = (repo / AGENTS_DIR / name).read_text()
        assert MANAGED_MARKER in content
        assert "byolsp agent-check" in content
        # SPEC 27.4: both harnesses auto-discover the rule-capture skill.
        assert "`byolsp` rule-capture skill" in content
        assert ".agents/skills/byolsp" in content


def test_claude_code_install_writes_a_guarded_project_hook(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "claude-code")

    command = claude_command(repo)
    assert f"{BYOLSP_COMMAND_SIGNATURE} claude-code" in command
    assert "command -v byolsp" in command
    # The skill render still creates .claude/skills/ alongside the hook.
    assert (repo / ".claude" / "skills" / "byolsp" / "SKILL.md").is_file()


def test_claude_code_install_merges_into_existing_settings(home: Path) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)
    settings = repo / SETTINGS_RELPATH
    settings.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": []}}))

    assert init_with_agents(repo, "claude-code") == 0

    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"] == []
    assert BYOLSP_COMMAND_SIGNATURE in json.dumps(data["hooks"]["PostToolUse"])

    snapshot = settings.read_text()
    assert init_with_agents(repo, "claude-code") == 0
    assert settings.read_text() == snapshot

    # The settings hook plus instruction file satisfy doctor's agent_files check.
    assert main(["doctor", "--repo", str(repo), "--quick"]) == 0


def test_outdated_claude_settings_hook_is_updated(home: Path) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)
    settings = repo / SETTINGS_RELPATH
    stale = {
        "matcher": "Write",
        "hooks": [{"type": "command", "command": "byolsp agent-check --stdin-hook x"}],
    }
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [stale]}}))

    assert init_with_agents(repo, "claude-code") == 0

    assert f"{BYOLSP_COMMAND_SIGNATURE} claude-code" in claude_command(repo)


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

    assert MANAGED_MARKER in (repo / AGENTS_DIR / "codex.md").read_text()
    assert (repo / ".codex" / "hooks.json").is_file()
    assert load_repo_config(repo).agents == ["skill", "codex"]


def test_cursor_is_a_full_agent_choice(home: Path) -> None:
    repo = make_repo(home)

    assert hook("install", repo, "cursor") == 0

    instructions = (repo / AGENTS_DIR / "cursor.md").read_text()
    assert MANAGED_MARKER in instructions
    assert "Cursor" in instructions
    assert "`byolsp` rule-capture skill" in instructions
    hooks = json.loads((repo / ".cursor" / "hooks.json").read_text())
    assert BYOLSP_COMMAND_SIGNATURE in json.dumps(hooks)
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

    assert hook("install", repo, "generic") == 1

    err = capsys.readouterr().err
    assert "byolsp init" in err
    assert "Traceback" not in err


def test_hook_uninstall_removes_only_marker_bearing_files(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    hook("install", repo, "copilot")
    (repo / AGENTS_DIR / "copilot.md").write_text("my own notes\n")

    assert hook("uninstall", repo, "copilot") == 0

    assert (repo / AGENTS_DIR / "copilot.md").read_text() == "my own notes\n"
    assert "without the BYOLSP marker" in capsys.readouterr().out
    assert load_repo_config(repo).agents == ["skill"]


def test_hook_uninstall_removes_the_installed_adapter_and_hook(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "codex")

    assert hook("uninstall", repo, "codex") == 0

    assert not (repo / AGENTS_DIR / "codex.md").exists()
    assert not (repo / ".codex" / "hooks.json").exists()
    assert load_repo_config(repo).agents == ["skill"]
    # Idempotent: a second uninstall has nothing to do and still succeeds.
    assert hook("uninstall", repo, "codex") == 0


def test_hook_uninstall_claude_code_removes_only_the_byolsp_settings_group(
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
    assert BYOLSP_COMMAND_SIGNATURE not in settings.read_text()
