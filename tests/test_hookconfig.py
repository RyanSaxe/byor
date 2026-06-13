"""The generalized per-harness hook-config engine."""

import json
from pathlib import Path

import pytest
from conftest import commands_in

from byor.errors import ConfigError
from byor.harness import HARNESS_CHOICES, Harness
from byor.hookconfig import (
    BYOR_COMMAND_SIGNATURE,
    HOOK_SPECS,
    global_hook_dir,
    hook_command,
    hook_installed,
    install_hook,
    uninstall_hook,
)


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A home directory the global hook configs land under, never the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def config_text(repo: Path, harness: Harness) -> str:
    return (repo / HOOK_SPECS[harness].project_relpath).read_text()


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_project_install_writes_a_guarded_command(
    harness: Harness, tmp_path: Path
) -> None:
    install_hook(tmp_path, harness, "project")

    [command] = commands_in(json.loads(config_text(tmp_path, harness)))
    assert f"{BYOR_COMMAND_SIGNATURE} {harness}" in command
    assert "command -v byor" in command
    assert command.endswith("|| true")
    assert hook_installed(tmp_path, harness, "project")


def test_claude_code_global_command_is_unguarded_and_redirects(tmp_path: Path) -> None:
    command = hook_command("claude-code", "global")

    assert command == f"{BYOR_COMMAND_SIGNATURE} claude-code >&2"


def test_only_project_scope_carries_the_teammate_guard() -> None:
    # The guard protects shared (committed) project configs; global and local
    # are personal, so they run byor directly.
    assert "command -v byor" in hook_command("claude-code", "project")
    assert "command -v byor" not in hook_command("claude-code", "local")
    assert "command -v byor" not in hook_command("claude-code", "global")


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_install_is_idempotent(harness: Harness, tmp_path: Path) -> None:
    install_hook(tmp_path, harness, "project")
    snapshot = config_text(tmp_path, harness)

    assert install_hook(tmp_path, harness, "project") == []
    assert config_text(tmp_path, harness) == snapshot


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_uninstall_removes_only_the_byor_entry(
    harness: Harness, tmp_path: Path
) -> None:
    spec = HOOK_SPECS[harness]
    user_entry: dict[str, object] = {"command": "echo mine"}
    if spec.matcher is not None:
        user_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
    path = tmp_path / spec.project_relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    config = _config_with_entry(spec.key_path, user_entry)
    path.write_text(json.dumps(config))
    install_hook(tmp_path, harness, "project")

    assert uninstall_hook(tmp_path, harness, "project")

    remaining = commands_in(json.loads(config_text(tmp_path, harness)))
    assert all(BYOR_COMMAND_SIGNATURE not in command for command in remaining)
    assert not hook_installed(tmp_path, harness, "project")


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_uninstall_is_idempotent_and_silent_when_absent(
    harness: Harness, tmp_path: Path
) -> None:
    assert uninstall_hook(tmp_path, harness, "project") == []


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_global_scope_writes_under_the_isolated_home(
    harness: Harness, isolated_home: Path, tmp_path: Path
) -> None:
    install_hook(tmp_path, harness, "global")

    spec = HOOK_SPECS[harness]
    global_path = global_hook_dir(harness, isolated_home) / spec.global_relpath
    assert global_path.is_file()
    [command] = commands_in(json.loads(global_path.read_text()))
    # Global configs are personal: no teammate guard.
    assert "command -v byor" not in command
    assert hook_installed(tmp_path, harness, "global")


def test_claude_local_scope_uses_settings_local(tmp_path: Path) -> None:
    install_hook(tmp_path, "claude-code", "local")

    local = tmp_path / ".claude" / "settings.local.json"
    assert local.is_file()
    assert BYOR_COMMAND_SIGNATURE in local.read_text()


def test_install_preserves_unrelated_keys_and_user_entries(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    user_group = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
    settings.write_text(
        json.dumps({"model": "opus", "hooks": {"PostToolUse": [user_group]}})
    )

    install_hook(tmp_path, "claude-code", "project")

    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert user_group in data["hooks"]["PostToolUse"]


def test_malformed_config_raises_a_clean_config_error(tmp_path: Path) -> None:
    path = tmp_path / ".cursor" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    with pytest.raises(ConfigError, match="not valid JSON"):
        install_hook(tmp_path, "cursor", "project")


def _config_with_entry(
    key_path: tuple[str, ...], entry: dict[str, object]
) -> dict[str, object]:
    node: dict[str, object] = {}
    config = node
    for key in key_path[:-1]:
        child: dict[str, object] = {}
        node[key] = child
        node = child
    node[key_path[-1]] = [entry]
    return config
