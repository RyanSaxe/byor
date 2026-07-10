"""The generalized per-harness hook-config engine (global registration).

Every harness now carries two hook events — the post-edit feedback hook and the pre-command gate —
and one install must write both without disturbing what already exists. Most harness configs are
user-owned JSON files byor merges entries into, so the cases here guard co-existence: idempotent
installs, a legacy post-edit-only file gaining only the pre-command entry, per-event signatures that
never cross-match, healing stale or user-mixed byor entries, and uninstalls that remove only byor's
entries. Copilot instead owns its whole config file, which flips the rules to overwrite-and-delete.
"""

import json
from pathlib import Path

import pytest
from support import commands_in

from byor.agents.harness import HARNESS_CHOICES, Harness
from byor.agents.hookconfig import (
    BYOR_COMMAND_SIGNATURE,
    BYOR_PRECOMMAND_SIGNATURE,
    HOOK_SPECS,
    global_hook_dir,
    hook_command,
    hook_installed,
    hook_problem,
    install_hook,
    uninstall_hook,
)
from byor.errors import ConfigError


@pytest.fixture(autouse=True)
# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def config_path(home: Path, harness: Harness) -> Path:
    return global_hook_dir(harness, home) / HOOK_SPECS[(harness, "post-edit")].global_relpath


# Harnesses whose config file may hold the user's own entries (so byor must
# preserve them); a byor-owned `byor.json` is excluded — byor owns it wholesale.
SHARED_HARNESSES = [h for h in HARNESS_CHOICES if not HOOK_SPECS[(h, "post-edit")].owns_file]


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_install_writes_both_events_with_unguarded_commands(harness: Harness, isolated_home: Path) -> None:
    install_hook(harness)

    commands = commands_in(json.loads(config_path(isolated_home, harness).read_text()))
    assert f"{BYOR_COMMAND_SIGNATURE} {harness}" in " ".join(commands)
    assert f"{BYOR_PRECOMMAND_SIGNATURE} {harness}" in " ".join(commands)
    # Global hooks are personal: no teammate guard, no `|| true`.
    assert all("command -v byor" not in command for command in commands)
    assert hook_installed(harness)


def test_claude_code_post_edit_command_redirects_to_stderr_but_the_gate_does_not() -> None:
    assert hook_command(HOOK_SPECS[("claude-code", "post-edit")]) == f"{BYOR_COMMAND_SIGNATURE} claude-code >&2"
    # The pre-command gate replies with a JSON permission decision on stdout;
    # a stderr redirect would swallow it.
    assert hook_command(HOOK_SPECS[("claude-code", "pre-command")]) == f"{BYOR_PRECOMMAND_SIGNATURE} claude-code"


@pytest.mark.parametrize("harness", ["codex", "copilot"])
def test_non_claude_commands_are_bare(harness: Harness) -> None:
    assert hook_command(HOOK_SPECS[(harness, "post-edit")]) == f"{BYOR_COMMAND_SIGNATURE} {harness}"


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_install_is_idempotent(harness: Harness, isolated_home: Path) -> None:
    install_hook(harness)
    snapshot = config_path(isolated_home, harness).read_text()

    assert install_hook(harness) == []
    assert config_path(isolated_home, harness).read_text() == snapshot


def test_a_legacy_post_edit_only_settings_gains_only_the_gate_entry(isolated_home: Path) -> None:
    # Upgrading from a pre-0.4 install: the existing PostToolUse group must stay
    # byte-identical while the PreToolUse gate is appended.
    install_hook("claude-code")
    path = config_path(isolated_home, "claude-code")
    data = json.loads(path.read_text())
    del data["hooks"]["PreToolUse"]
    path.write_text(json.dumps(data, indent=2) + "\n")
    legacy_post_edit = json.loads(path.read_text())["hooks"]["PostToolUse"]

    messages = install_hook("claude-code")

    upgraded = json.loads(path.read_text())["hooks"]
    assert upgraded["PostToolUse"] == legacy_post_edit
    assert BYOR_PRECOMMAND_SIGNATURE in json.dumps(upgraded["PreToolUse"])
    assert messages == ["Installed a claude-code pre-command hook in ~/.claude/settings.json"]


def test_stale_bare_byor_entry_is_not_current(isolated_home: Path) -> None:
    spec = HOOK_SPECS[("codex", "post-edit")]
    path = config_path(isolated_home, "codex")
    path.parent.mkdir(parents=True)
    stale: dict[str, object] = {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": f"{BYOR_COMMAND_SIGNATURE} codex"}],
    }
    path.write_text(json.dumps(_config_with_entry(spec.key_path, stale)))

    assert hook_installed("codex") is False
    assert hook_problem("codex") == "the codex post-edit hook is out of date"

    install_hook("codex")

    data = json.loads(path.read_text())
    matcher = spec.matcher
    assert matcher is not None
    assert matcher in json.dumps(data)
    assert hook_installed("codex")


def test_hook_problem_names_the_missing_event(isolated_home: Path) -> None:
    install_hook("claude-code")
    path = config_path(isolated_home, "claude-code")
    data = json.loads(path.read_text())
    del data["hooks"]["PreToolUse"]
    path.write_text(json.dumps(data))

    assert hook_problem("claude-code") == "the claude-code pre-command hook is not installed"


def test_user_entry_mixing_in_the_byor_command_is_healthy(isolated_home: Path) -> None:
    # install_hook deliberately leaves a user-edited entry alone, so doctor must
    # not report it as out of date — that FAIL could never be fixed.
    install_hook("claude-code")
    spec = HOOK_SPECS[("claude-code", "post-edit")]
    path = config_path(isolated_home, "claude-code")
    data = json.loads(path.read_text())
    data["hooks"]["PostToolUse"] = [
        {
            "matcher": "Write|Edit",
            "hooks": [
                {"type": "command", "command": "my-own-formatter"},
                {"type": "command", "command": hook_command(spec)},
            ],
        }
    ]
    path.write_text(json.dumps(data))

    assert hook_problem("claude-code") is None
    assert hook_installed("claude-code")
    assert install_hook("claude-code") == []


def test_per_event_signatures_do_not_cross_match(isolated_home: Path) -> None:
    # A pre-command entry must never satisfy the post-edit spec (or vice versa):
    # a settings.json holding only the gate still needs the feedback hook.
    install_hook("claude-code")
    path = config_path(isolated_home, "claude-code")
    data = json.loads(path.read_text())
    del data["hooks"]["PostToolUse"]
    path.write_text(json.dumps(data))

    assert hook_problem("claude-code") == "the claude-code post-edit hook is not installed"


@pytest.mark.parametrize("harness", SHARED_HARNESSES)
def test_uninstall_removes_only_the_byor_entries(harness: Harness, isolated_home: Path) -> None:
    spec = HOOK_SPECS[(harness, "post-edit")]
    user_entry: dict[str, object] = {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
    path = config_path(isolated_home, harness)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_config_with_entry(spec.key_path, user_entry)))
    install_hook(harness)

    assert uninstall_hook(harness)

    remaining = commands_in(json.loads(config_path(isolated_home, harness).read_text()))
    assert all(BYOR_COMMAND_SIGNATURE not in command for command in remaining)
    assert all(BYOR_PRECOMMAND_SIGNATURE not in command for command in remaining)
    assert not hook_installed(harness)


@pytest.mark.parametrize("harness", HARNESS_CHOICES)
def test_uninstall_is_idempotent_and_silent_when_absent(harness: Harness) -> None:
    assert uninstall_hook(harness) == []


def test_install_preserves_unrelated_keys_and_user_entries(isolated_home: Path) -> None:
    settings = config_path(isolated_home, "claude-code")
    settings.parent.mkdir(parents=True)
    user_post_edit = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
    user_gate = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-guard"}]}
    settings.write_text(
        json.dumps({"model": "opus", "hooks": {"PostToolUse": [user_post_edit], "PreToolUse": [user_gate]}})
    )

    install_hook("claude-code")

    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert user_post_edit in data["hooks"]["PostToolUse"]
    assert user_gate in data["hooks"]["PreToolUse"]


def test_copilot_writes_the_documented_envelope(isolated_home: Path) -> None:
    install_hook("copilot")

    data = json.loads(config_path(isolated_home, "copilot").read_text())
    assert data == {
        "version": 1,
        "hooks": {
            "postToolUse": [{"type": "command", "command": hook_command(HOOK_SPECS[("copilot", "post-edit")])}],
            "preToolUse": [{"type": "command", "command": hook_command(HOOK_SPECS[("copilot", "pre-command")])}],
        },
    }


def test_copilot_owned_install_overwrites_a_stale_single_event_file(isolated_home: Path) -> None:
    # Upgrading over an old byor-written file must not leave a stale top-level
    # key or a missing pre-command list.
    path = config_path(isolated_home, "copilot")
    path.parent.mkdir(parents=True)
    old_command = hook_command(HOOK_SPECS[("copilot", "post-edit")])
    path.write_text(json.dumps({"postToolUse": [{"command": old_command}]}))

    install_hook("copilot")

    data = json.loads(path.read_text())
    assert "postToolUse" not in data  # the stale top-level key is gone
    assert data["hooks"]["postToolUse"] == [{"type": "command", "command": old_command}]
    assert BYOR_PRECOMMAND_SIGNATURE in json.dumps(data["hooks"]["preToolUse"])


def test_copilot_uninstall_deletes_its_owned_file(isolated_home: Path) -> None:
    install_hook("copilot")
    assert uninstall_hook("copilot")
    assert not config_path(isolated_home, "copilot").exists()


def test_claude_code_matcher_excludes_notebook_edit() -> None:
    # NotebookEdit payloads carry notebook_path/new_source, which the parser
    # cannot read, so the hook must not subscribe to them and scan nothing.
    matcher = HOOK_SPECS[("claude-code", "post-edit")].matcher
    assert matcher is not None
    assert "NotebookEdit" not in matcher


def test_malformed_config_raises_a_clean_config_error(isolated_home: Path) -> None:
    path = config_path(isolated_home, "codex")
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    with pytest.raises(ConfigError, match="not valid JSON"):
        install_hook("codex")


def _config_with_entry(key_path: tuple[str, ...], entry: dict[str, object]) -> dict[str, object]:
    node: dict[str, object] = {}
    config = node
    for key in key_path[:-1]:
        child: dict[str, object] = {}
        node[key] = child
        node = child
    node[key_path[-1]] = [entry]
    return config
