"""The generalized per-harness hook-config engine (global registration)."""

import json
from pathlib import Path

import pytest
from support import commands_in

from byor.agents.harness import HARNESS_CHOICES, Harness
from byor.agents.hookconfig import (
    BYOR_COMMAND_SIGNATURE,
    HOOK_SPECS,
    global_hook_dir,
    hook_command,
    hook_installed,
    install_hook,
    uninstall_hook,
)
from byor.errors import ConfigError


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A home directory the global hook configs land under, never the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def config_path(home: Path, harness: Harness) -> Path:
    return global_hook_dir(harness, home) / HOOK_SPECS[harness].global_relpath


# Harnesses whose config file may hold the user's own entries (so byor must
# preserve them); a byor-owned `byor.json` is excluded — byor owns it wholesale.
SHARED_HARNESSES = [h for h in HARNESS_CHOICES if not HOOK_SPECS[h].owns_file]


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_install_writes_an_unguarded_command(
    harness: Harness, isolated_home: Path
) -> None:
    install_hook(harness)

    [command] = commands_in(json.loads(config_path(isolated_home, harness).read_text()))
    assert f"{BYOR_COMMAND_SIGNATURE} {harness}" in command
    # Global hooks are personal: no teammate guard, no `|| true`.
    assert "command -v byor" not in command
    assert hook_installed(harness)


def test_claude_code_command_redirects_to_stderr() -> None:
    assert hook_command("claude-code") == f"{BYOR_COMMAND_SIGNATURE} claude-code >&2"


@pytest.mark.parametrize("harness", ["codex", "copilot", "cursor"])
def test_non_claude_commands_are_bare(harness: Harness) -> None:
    assert hook_command(harness) == f"{BYOR_COMMAND_SIGNATURE} {harness}"


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_install_is_idempotent(harness: Harness, isolated_home: Path) -> None:
    install_hook(harness)
    snapshot = config_path(isolated_home, harness).read_text()

    assert install_hook(harness) == []
    assert config_path(isolated_home, harness).read_text() == snapshot


@pytest.mark.parametrize("harness", SHARED_HARNESSES)
def test_uninstall_removes_only_the_byor_entry(
    harness: Harness, isolated_home: Path
) -> None:
    spec = HOOK_SPECS[harness]
    user_entry: dict[str, object] = {"command": "echo mine"}
    if spec.matcher is not None:
        user_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
    path = config_path(isolated_home, harness)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_config_with_entry(spec.key_path, user_entry)))
    install_hook(harness)

    assert uninstall_hook(harness)

    remaining = commands_in(json.loads(config_path(isolated_home, harness).read_text()))
    assert all(BYOR_COMMAND_SIGNATURE not in command for command in remaining)
    assert not hook_installed(harness)


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_uninstall_is_idempotent_and_silent_when_absent(harness: Harness) -> None:
    assert uninstall_hook(harness) == []


def test_install_preserves_unrelated_keys_and_user_entries(isolated_home: Path) -> None:
    settings = config_path(isolated_home, "claude-code")
    settings.parent.mkdir(parents=True)
    user_group = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
    settings.write_text(
        json.dumps({"model": "opus", "hooks": {"PostToolUse": [user_group]}})
    )

    install_hook("claude-code")

    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert user_group in data["hooks"]["PostToolUse"]


def test_copilot_writes_the_documented_envelope(isolated_home: Path) -> None:
    install_hook("copilot")

    data = json.loads(config_path(isolated_home, "copilot").read_text())
    assert data == {
        "version": 1,
        "hooks": {
            "postToolUse": [{"type": "command", "command": hook_command("copilot")}]
        },
    }


def test_copilot_owned_install_overwrites_a_stale_file(isolated_home: Path) -> None:
    # Upgrading over an old byor-written file must not leave a stale top-level key.
    path = config_path(isolated_home, "copilot")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"postToolUse": [{"command": hook_command("copilot")}]}))

    install_hook("copilot")

    data = json.loads(path.read_text())
    assert "postToolUse" not in data  # the stale top-level key is gone
    assert data["hooks"]["postToolUse"] == [
        {"type": "command", "command": hook_command("copilot")}
    ]


def test_copilot_uninstall_deletes_its_owned_file(isolated_home: Path) -> None:
    install_hook("copilot")
    assert uninstall_hook("copilot")
    assert not config_path(isolated_home, "copilot").exists()


def test_malformed_config_raises_a_clean_config_error(isolated_home: Path) -> None:
    path = config_path(isolated_home, "cursor")
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    with pytest.raises(ConfigError, match="not valid JSON"):
        install_hook("cursor")


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
