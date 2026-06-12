"""AI agent adapters and the `byolsp hook` command (SPEC 15.10, 16)."""

import json
from pathlib import Path

import pytest
from conftest import make_repo

from byolsp.agents import CLAUDE_HOOK_COMMAND, MANAGED_MARKER
from byolsp.cli import main
from byolsp.config import load_repo_config

AGENTS_DIR = Path(".byolsp") / "agents"


def init_with_agents(repo: Path, agents: str) -> int:
    return main(["init", "--repo", str(repo), "--non-interactive", "--agents", agents])


def test_init_installs_instruction_files_for_requested_agents(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "codex,copilot")

    for name in ("codex.md", "copilot.md"):
        content = (repo / AGENTS_DIR / name).read_text()
        assert MANAGED_MARKER in content
        assert "byolsp agent-check --files <changed files>" in content


def test_claude_code_without_claude_dir_writes_wiring_instructions(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "claude-code")

    content = (repo / AGENTS_DIR / "claude-code.md").read_text()
    assert MANAGED_MARKER in content
    assert "PostToolUse" in content
    assert not (repo / ".claude").exists()


def test_claude_code_with_claude_dir_merges_settings_hook(home: Path) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)
    settings = repo / ".claude" / "settings.json"
    settings.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": []}}))

    assert init_with_agents(repo, "claude-code") == 0

    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"] == []
    [group] = data["hooks"]["PostToolUse"]
    assert group["hooks"][0]["command"] == CLAUDE_HOOK_COMMAND
    assert not (repo / AGENTS_DIR / "claude-code.md").exists()

    snapshot = settings.read_text()
    assert init_with_agents(repo, "claude-code") == 0
    assert settings.read_text() == snapshot

    # The settings hook satisfies doctor's agent_files check.
    assert main(["doctor", "--repo", str(repo), "--quick"]) == 0


def test_outdated_claude_settings_hook_is_updated(home: Path) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)
    settings = repo / ".claude" / "settings.json"
    stale = {
        "matcher": "Write",
        "hooks": [{"type": "command", "command": "byolsp agent-check --files old"}],
    }
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [stale]}}))

    assert init_with_agents(repo, "claude-code") == 0

    [group] = json.loads(settings.read_text())["hooks"]["PostToolUse"]
    assert group["hooks"][0]["command"] == CLAUDE_HOOK_COMMAND


def test_claude_code_creates_settings_when_only_the_dir_exists(home: Path) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)

    assert init_with_agents(repo, "claude-code") == 0

    data = json.loads((repo / ".claude" / "settings.json").read_text())
    assert "byolsp agent-check" in json.dumps(data["hooks"]["PostToolUse"])


def test_invalid_claude_settings_fail_cleanly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text("{not json")

    assert init_with_agents(repo, "claude-code") == 1

    err = capsys.readouterr().err
    assert ".claude/settings.json is not valid JSON" in err
    assert "Traceback" not in err


def hook(action: str, repo: Path, agent: str) -> int:
    return main(["hook", action, "--repo", str(repo), "--agent", agent])


def test_hook_install_writes_the_adapter_and_records_the_agent(home: Path) -> None:
    repo = make_repo(home)

    assert hook("install", repo, "codex") == 0

    assert MANAGED_MARKER in (repo / AGENTS_DIR / "codex.md").read_text()
    assert load_repo_config(repo).agents == ["codex"]


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
    assert load_repo_config(repo).agents == []


def test_hook_uninstall_removes_the_installed_adapter(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "codex")

    assert hook("uninstall", repo, "codex") == 0

    assert not (repo / AGENTS_DIR / "codex.md").exists()
    assert load_repo_config(repo).agents == []
    # Idempotent: a second uninstall has nothing to do and still succeeds.
    assert hook("uninstall", repo, "codex") == 0


def test_hook_uninstall_claude_code_removes_only_the_byolsp_settings_group(
    home: Path,
) -> None:
    repo = make_repo(home)
    user_group = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
    (repo / ".claude").mkdir()
    settings = repo / ".claude" / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [user_group]}}))
    hook("install", repo, "claude-code")

    assert hook("uninstall", repo, "claude-code") == 0

    data = json.loads(settings.read_text())
    assert data["hooks"]["PostToolUse"] == [user_group]
    assert "byolsp agent-check" not in settings.read_text()
