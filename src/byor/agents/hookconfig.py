"""Manage global hook configuration files for AI agents.

BYOR installs exact hook entries into each harness configuration and later verifies that those
entries still match the current package. This module owns that file shape so install, doctor, and
self-heal agree on one contract. Each harness carries one spec per hook event — the post-edit
feedback hook and the pre-command gate — and the per-harness API (install, uninstall, problem)
iterates both, so callers stay event-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from byor.errors import ConfigError
from byor.io.fsio import write_text_atomic

if TYPE_CHECKING:
    from collections.abc import Sequence

    from byor.agents.harness import Harness, JsonValue

__all__ = (
    "HookEvent",
    "HookSpec",
    "global_hook_dir",
    "hook_command",
    "hook_installed",
    "hook_problem",
    "install_hook",
    "uninstall_hook",
)

# agent-check and command-check fail open and exit 0 for every harness except
# claude-code's post-edit hook (which reads exit 2 + stderr), so no shell
# `|| true` guard is needed on the commands. Neither signature is a substring
# of the other, so per-event detection cannot cross-match.
BYOR_COMMAND_SIGNATURE = "byor agent-check --stdin-hook"
BYOR_PRECOMMAND_SIGNATURE = "byor command-check --stdin-hook"

HookEvent = Literal["post-edit", "pre-command"]


@dataclass(frozen=True)
class HookSpec:
    harness: Harness
    event: HookEvent
    signature: str
    """The byor command this spec installs and detects, without the harness arg."""
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


HOOK_SPECS: dict[tuple[Harness, HookEvent], HookSpec] = {
    ("claude-code", "post-edit"): HookSpec(
        harness="claude-code",
        event="post-edit",
        signature=BYOR_COMMAND_SIGNATURE,
        global_relpath="settings.json",
        key_path=("hooks", "PostToolUse"),
        matcher="Write|Edit|MultiEdit",
        nests_command=True,
        stderr_feedback=True,
    ),
    ("claude-code", "pre-command"): HookSpec(
        harness="claude-code",
        event="pre-command",
        signature=BYOR_PRECOMMAND_SIGNATURE,
        global_relpath="settings.json",
        key_path=("hooks", "PreToolUse"),
        matcher="Bash",
        nests_command=True,
    ),
    ("codex", "post-edit"): HookSpec(
        harness="codex",
        event="post-edit",
        signature=BYOR_COMMAND_SIGNATURE,
        global_relpath="hooks.json",
        key_path=("hooks", "PostToolUse"),
        matcher="apply_patch|Edit|Write",
        nests_command=True,
    ),
    ("codex", "pre-command"): HookSpec(
        harness="codex",
        event="pre-command",
        signature=BYOR_PRECOMMAND_SIGNATURE,
        global_relpath="hooks.json",
        key_path=("hooks", "PreToolUse"),
        matcher="shell|local_shell|exec_command",
        nests_command=True,
    ),
    ("copilot", "post-edit"): HookSpec(
        harness="copilot",
        event="post-edit",
        signature=BYOR_COMMAND_SIGNATURE,
        global_relpath="hooks/byor.json",
        key_path=("hooks", "postToolUse"),
        matcher=None,
        nests_command=False,
        typed_entry=True,
        root_fields={"version": 1},
        owns_file=True,
    ),
    ("copilot", "pre-command"): HookSpec(
        harness="copilot",
        event="pre-command",
        signature=BYOR_PRECOMMAND_SIGNATURE,
        global_relpath="hooks/byor.json",
        key_path=("hooks", "preToolUse"),
        matcher=None,
        nests_command=False,
        typed_entry=True,
        root_fields={"version": 1},
        owns_file=True,
    ),
}


def install_hook(harness: Harness) -> list[str]:
    specs = _harness_specs(harness)
    if specs[0].owns_file:
        return _install_owned_hooks(harness, specs)
    messages: list[str] = []
    for spec in specs:
        messages.extend(_install_spec(spec))
    return messages


def _install_spec(spec: HookSpec) -> list[str]:
    path = _config_path(spec)
    relpath = _display_relpath(spec)
    config = _load_config(path, relpath)
    entries = _entries(config, spec, relpath=relpath)
    current = _byor_entry(spec)
    if current in entries:
        return []
    kept = [entry for entry in entries if not _is_byor_entry(entry, signature=spec.signature)]
    if any(_contains_byor_command(entry, signature=spec.signature) for entry in kept):
        return []
    _set_entries(config, spec, entries=[*kept, current])
    _save_config(path, config)
    return [f"Installed a {spec.harness} {spec.event} hook in {relpath}"]


def _install_owned_hooks(harness: Harness, specs: list[HookSpec]) -> list[str]:
    # All owned specs for a harness share one byor-owned file; install compares
    # the whole file to the union of their entries, so an old single-event file
    # is upgraded wholesale on the next install or self-heal.
    spec = specs[0]
    path = _config_path(spec)
    relpath = _display_relpath(spec)
    desired = _owned_config(specs)
    if path.is_file() and _load_config(path, relpath) == desired:
        return []
    _save_config(path, desired)
    events = " and ".join(owned.event for owned in specs)
    return [f"Installed the {harness} {events} hooks in {relpath}"]


def _owned_config(specs: list[HookSpec]) -> dict[str, JsonValue]:
    config: dict[str, JsonValue] = dict(specs[0].root_fields)
    for spec in specs:
        _set_entries(config, spec, entries=[_byor_entry(spec)])
    return config


def uninstall_hook(harness: Harness) -> list[str]:
    specs = _harness_specs(harness)
    if specs[0].owns_file:
        return _uninstall_owned_hooks(harness, specs)
    messages: list[str] = []
    for spec in specs:
        messages.extend(_uninstall_spec(spec))
    return messages


def _uninstall_spec(spec: HookSpec) -> list[str]:
    path = _config_path(spec)
    if not path.is_file():
        return []
    relpath = _display_relpath(spec)
    config = _load_config(path, relpath)
    entries = _entries(config, spec, relpath=relpath)
    kept = [entry for entry in entries if not _is_byor_entry(entry, signature=spec.signature)]
    if len(kept) == len(entries):
        return []
    _set_entries(config, spec, entries=kept)
    if config:
        _save_config(path, config)
    else:
        path.unlink()
    return [f"Removed the {spec.harness} {spec.event} hook from {relpath}"]


def _uninstall_owned_hooks(harness: Harness, specs: list[HookSpec]) -> list[str]:
    spec = specs[0]
    path = _config_path(spec)
    if not path.is_file():
        return []
    relpath = _display_relpath(spec)
    config = _load_config(path, relpath)
    # Hooks are managed per-agent, not per-event: any byor entry in the owned
    # file (a legacy single-event one included) means the whole file goes.
    if not any(_spec_has_byor_entry(config, owned, relpath=relpath) for owned in specs):
        return []
    path.unlink()
    return [f"Removed the {harness} hooks from {relpath}"]


def _spec_has_byor_entry(config: dict[str, JsonValue], spec: HookSpec, *, relpath: str) -> bool:
    return any(
        _contains_byor_command(entry, signature=spec.signature) for entry in _entries(config, spec, relpath=relpath)
    )


def hook_installed(harness: Harness) -> bool:
    return hook_problem(harness) is None


def hook_problem(harness: Harness) -> str | None:
    for spec in _harness_specs(harness):
        problem = _spec_problem(spec)
        if problem is not None:
            return problem
    return None


def _spec_problem(spec: HookSpec) -> str | None:
    path = _config_path(spec)
    if not path.is_file():
        return f"the {spec.harness} {spec.event} hook is not installed"
    relpath = _display_relpath(spec)
    entries = _entries(_load_config(path, relpath), spec, relpath=relpath)
    if _byor_entry(spec) in entries:
        return None
    # An entry the user mixed their own commands into is user-owned: install_hook
    # leaves it alone, so doctor must treat it as healthy rather than demand a
    # reinstall that would never change anything.
    if any(
        _contains_byor_command(entry, signature=spec.signature) and not _is_byor_entry(entry, signature=spec.signature)
        for entry in entries
    ):
        return None
    if any(_is_byor_entry(entry, signature=spec.signature) for entry in entries):
        return f"the {spec.harness} {spec.event} hook is out of date"
    return f"the {spec.harness} {spec.event} hook is not installed"


def hook_command(spec: HookSpec) -> str:
    base = f"{spec.signature} {spec.harness}"
    return f"{base} >&2" if spec.stderr_feedback else base


def global_hook_dir(harness: Harness, home: Path) -> Path:
    return home / _GLOBAL_DIRS[harness]


def _harness_specs(harness: Harness) -> list[HookSpec]:
    return [spec for (name, _event), spec in HOOK_SPECS.items() if name == harness]


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
    command = hook_command(spec)
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


def _is_byor_entry(entry: JsonValue, *, signature: str) -> bool:
    commands = _entry_commands(entry)
    return bool(commands) and all(_is_byor_command(command, signature=signature) for command in commands)


def _contains_byor_command(entry: JsonValue, *, signature: str) -> bool:
    return any(_is_byor_command(command, signature=signature) for command in _entry_commands(entry))


def _entry_commands(entry: JsonValue) -> Sequence[JsonValue]:
    if not isinstance(entry, dict):
        return []
    nested = entry.get("hooks")
    if isinstance(nested, list):
        return [hook.get("command") for hook in nested if isinstance(hook, dict)]
    return [entry.get("command")]


def _is_byor_command(command: JsonValue, *, signature: str) -> bool:
    return isinstance(command, str) and signature in command
