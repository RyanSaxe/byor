"""Manage global hook configuration files for AI agents.

BYOR installs exact hook entries into each harness configuration and later verifies that those
entries still match the current package. This module owns that file shape so install, doctor, and
self-heal agree on one contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from byor.errors import ConfigError
from byor.io.fsio import write_text_atomic

if TYPE_CHECKING:
    from collections.abc import Sequence

    from byor.agents.harness import Harness, JsonValue

__all__ = (
    "HookSpec",
    "global_hook_dir",
    "hook_command",
    "hook_installed",
    "hook_problem",
    "install_hook",
    "uninstall_hook",
)

# agent-check fails open and exits 0 for every harness except claude-code (which
# reads exit 2 + stderr), so no shell `|| true` guard is needed on the command.
BYOR_COMMAND_SIGNATURE = "byor agent-check --stdin-hook"


@dataclass(frozen=True)
class HookSpec:
    harness: Harness
    global_relpath: str
    """Path relative to the harness's global config dir."""
    key_path: tuple[str, ...]
    """JSON pointer to the entry list inside the config object."""
    matcher: str | None
    """An entry's matcher value, or None for harnesses without matchers."""
    nests_command: bool
    """True when the command lives in a nested hooks[] list (claude/codex)."""
    stderr_feedback: bool = False
    """True when the harness reads feedback from stderr + exit 2, not stdout JSON."""
    typed_entry: bool = False
    """True when a flat entry must carry `"type": "command"` (copilot)."""
    root_fields: dict[str, JsonValue] = field(default_factory=dict)
    """Top-level keys the config file requires (e.g. `{"version": 1}`)."""
    owns_file: bool = False
    """True when the file is byor's alone (a dedicated `byor.json`), so install
    writes it wholesale and uninstall deletes it — no user entries to preserve."""


HOOK_SPECS: dict[Harness, HookSpec] = {
    "claude-code": HookSpec(
        harness="claude-code",
        global_relpath="settings.json",
        key_path=("hooks", "PostToolUse"),
        matcher="Write|Edit|MultiEdit",
        nests_command=True,
        stderr_feedback=True,
    ),
    "codex": HookSpec(
        harness="codex",
        global_relpath="hooks.json",
        key_path=("hooks", "PostToolUse"),
        matcher="apply_patch|Edit|Write",
        nests_command=True,
    ),
    "copilot": HookSpec(
        harness="copilot",
        global_relpath="hooks/byor.json",
        key_path=("hooks", "postToolUse"),
        matcher=None,
        nests_command=False,
        typed_entry=True,
        root_fields={"version": 1},
        owns_file=True,
    ),
}


def install_hook(harness: Harness) -> list[str]:
    spec = HOOK_SPECS[harness]
    path = _config_path(spec)
    relpath = _display_relpath(spec)
    if spec.owns_file:
        return _install_owned_hook(spec, path, relpath=relpath)
    config = _load_config(path, relpath)
    entries = _entries(config, spec, relpath=relpath)
    current = _byor_entry(spec)
    if current in entries:
        return []
    kept = [entry for entry in entries if not _is_byor_entry(entry)]
    if any(_contains_byor_command(entry) for entry in kept):
        return []
    _set_entries(config, spec, entries=[*kept, current])
    _save_config(path, config)
    return [f"Installed a {harness} post-edit hook in {relpath}"]


def _install_owned_hook(spec: HookSpec, path: Path, *, relpath: str) -> list[str]:
    desired = _owned_config(spec)
    if path.is_file() and _load_config(path, relpath) == desired:
        return []
    _save_config(path, desired)
    return [f"Installed a {spec.harness} post-edit hook in {relpath}"]


def _owned_config(spec: HookSpec) -> dict[str, JsonValue]:
    config: dict[str, JsonValue] = dict(spec.root_fields)
    _set_entries(config, spec, entries=[_byor_entry(spec)])
    return config


def uninstall_hook(harness: Harness) -> list[str]:
    spec = HOOK_SPECS[harness]
    path = _config_path(spec)
    relpath = _display_relpath(spec)
    if not path.is_file():
        return []
    if spec.owns_file:
        if not hook_installed(harness):
            return []
        path.unlink()
        return [f"Removed the {harness} post-edit hook from {relpath}"]
    config = _load_config(path, relpath)
    entries = _entries(config, spec, relpath=relpath)
    kept = [entry for entry in entries if not _is_byor_entry(entry)]
    if len(kept) == len(entries):
        return []
    _set_entries(config, spec, entries=kept)
    if config:
        _save_config(path, config)
    else:
        path.unlink()
    return [f"Removed the {harness} post-edit hook from {relpath}"]


def hook_installed(harness: Harness) -> bool:
    return hook_problem(harness) is None


def hook_problem(harness: Harness) -> str | None:
    spec = HOOK_SPECS[harness]
    path = _config_path(spec)
    if not path.is_file():
        return f"the {harness} hook is not installed"
    relpath = _display_relpath(spec)
    entries = _entries(_load_config(path, relpath), spec, relpath=relpath)
    if _byor_entry(spec) in entries:
        return None
    if any(_contains_byor_command(entry) for entry in entries):
        return f"the {harness} hook is out of date"
    return f"the {harness} hook is not installed"


def hook_command(harness: Harness) -> str:
    spec = HOOK_SPECS[harness]
    base = f"{BYOR_COMMAND_SIGNATURE} {harness}"
    return f"{base} >&2" if spec.stderr_feedback else base


def global_hook_dir(harness: Harness, home: Path) -> Path:
    return home / _GLOBAL_DIRS[harness]


_GLOBAL_DIRS: dict[Harness, str] = {
    "claude-code": ".claude",
    "codex": ".codex",
    "copilot": ".copilot",
}


def _config_path(spec: HookSpec) -> Path:
    return global_hook_dir(spec.harness, Path.home()) / spec.global_relpath


def _display_relpath(spec: HookSpec) -> str:
    return f"~/{_GLOBAL_DIRS[spec.harness]}/{spec.global_relpath}"


def _byor_entry(spec: HookSpec) -> dict[str, JsonValue]:
    command = hook_command(spec.harness)
    if spec.nests_command:
        entry: dict[str, JsonValue] = {"hooks": [{"type": "command", "command": command}]}
    elif spec.typed_entry:
        entry = {"type": "command", "command": command}
    else:
        entry = {"command": command}
    if spec.matcher is not None:
        return {"matcher": spec.matcher, **entry}
    return entry


def _load_config(path: Path, relpath: str) -> dict[str, JsonValue]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        msg = f"{relpath} is not valid JSON: {error}"
        raise ConfigError(msg) from error
    if not isinstance(data, dict):
        msg = f"{relpath}: expected a JSON object at the top level"
        raise ConfigError(msg)
    return data


def _save_config(path: Path, config: dict[str, JsonValue]) -> None:
    content = json.dumps(config, indent=2) + "\n"
    write_text_atomic(path, content)


def _entries(config: dict[str, JsonValue], spec: HookSpec, *, relpath: str) -> list[JsonValue]:
    node: JsonValue = config
    for key in spec.key_path:
        if not isinstance(node, dict):
            msg = f"{relpath}: expected '{key}' under a JSON object"
            raise ConfigError(msg)
        child = node.get(key)
        if child is None:
            return []
        node = child
    if not isinstance(node, list):
        msg = f"{relpath}: expected {'.'.join(spec.key_path)} to be a list"
        raise ConfigError(msg)
    return node


def _set_entries(config: dict[str, JsonValue], spec: HookSpec, *, entries: list[JsonValue]) -> None:
    *parents, leaf = spec.key_path
    node = config
    for key in parents:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    if entries:
        node[leaf] = entries
    else:
        node.pop(leaf, None)
    _prune_empty(config, spec.key_path)


def _prune_empty(config: dict[str, JsonValue], key_path: tuple[str, ...]) -> None:
    for depth in range(len(key_path) - 1, 0, -1):
        parent = _descend(config, key_path[:depth])
        if isinstance(parent, dict) and parent == {}:
            grandparent = _descend(config, key_path[: depth - 1])
            if isinstance(grandparent, dict):
                grandparent.pop(key_path[depth - 1], None)


def _descend(config: dict[str, JsonValue], keys: tuple[str, ...]) -> JsonValue:
    node: JsonValue = config
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _is_byor_entry(entry: JsonValue) -> bool:
    commands = _entry_commands(entry)
    return bool(commands) and all(_is_byor_command(command) for command in commands)


def _contains_byor_command(entry: JsonValue) -> bool:
    return any(_is_byor_command(command) for command in _entry_commands(entry))


def _entry_commands(entry: JsonValue) -> Sequence[JsonValue]:
    if not isinstance(entry, dict):
        return []
    nested = entry.get("hooks")
    if isinstance(nested, list):
        return [hook.get("command") for hook in nested if isinstance(hook, dict)]
    return [entry.get("command")]


def _is_byor_command(command: JsonValue) -> bool:
    return isinstance(command, str) and BYOR_COMMAND_SIGNATURE in command
