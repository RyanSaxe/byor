"""Per-harness post-edit hook configs, one converge-only-ours pipeline.

Every harness stores its hooks as a JSON list of entries — at a harness-specific
key path inside a harness-specific file — where each entry carries a shell
command. byor owns the entries whose command runs `byor agent-check`, and
converges them to the current command without touching entries a user added.

Registration is global: byor writes the hook once into the harness's home
config (`~/.claude/settings.json`, `~/.codex/hooks.json`, ...) so it fires in
every repo. `HookSpec` captures the edges that differ per harness (file
location, key path, entry shape, and command string); `install_hook`/
`uninstall_hook` drive the shared logic.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from byor.agents.harness import Harness, JsonValue
from byor.errors import ConfigError
from byor.io.fsio import write_text_atomic

# agent-check fails open and exits 0 for every harness except claude-code (which
# reads exit 2 + stderr), so no shell `|| true` guard is needed on the command.
BYOR_COMMAND_SIGNATURE = "byor agent-check --stdin-hook"


@dataclass(frozen=True)
class HookSpec:
    """The per-harness edges of the shared converge pipeline."""

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
    """Converge the harness's byor hook entry into its global config."""
    spec = HOOK_SPECS[harness]
    path = _config_path(spec)
    relpath = _display_relpath(spec)
    if spec.owns_file:
        return _install_owned_hook(spec, path, relpath)
    config = _load_config(path, relpath)
    entries = _entries(config, spec, relpath)
    current = _byor_entry(spec)
    if current in entries:
        return []
    kept = [entry for entry in entries if not _is_byor_entry(entry)]
    if any(_contains_byor_command(entry) for entry in kept):
        return []
    _set_entries(config, spec, [*kept, current])
    _save_config(path, config)
    return [f"Installed a {harness} post-edit hook in {relpath}"]


def _install_owned_hook(spec: HookSpec, path: Path, relpath: str) -> list[str]:
    """Write a byor-dedicated hook file wholesale; its name (`byor.json`) is ours."""
    desired = _owned_config(spec)
    if path.is_file() and _load_config(path, relpath) == desired:
        return []
    _save_config(path, desired)
    return [f"Installed a {spec.harness} post-edit hook in {relpath}"]


def _owned_config(spec: HookSpec) -> dict[str, JsonValue]:
    config: dict[str, JsonValue] = dict(spec.root_fields)
    _set_entries(config, spec, [_byor_entry(spec)])
    return config


def uninstall_hook(harness: Harness) -> list[str]:
    """Drop byor-owned entries from the global config; user entries stay."""
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
    entries = _entries(config, spec, relpath)
    kept = [entry for entry in entries if not _is_byor_entry(entry)]
    if len(kept) == len(entries):
        return []
    _set_entries(config, spec, kept)
    if config:
        _save_config(path, config)
    else:
        path.unlink()
    return [f"Removed the {harness} post-edit hook from {relpath}"]


def hook_installed(harness: Harness) -> bool:
    """Whether a byor-owned entry is present in the harness's global config."""
    spec = HOOK_SPECS[harness]
    path = _config_path(spec)
    if not path.is_file():
        return False
    relpath = _display_relpath(spec)
    entries = _entries(_load_config(path, relpath), spec, relpath)
    return any(_contains_byor_command(entry) for entry in entries)


def hook_command(harness: Harness) -> str:
    """The shell command an entry runs.

    Global hooks are personal, so there is no teammate guard; claude-code reads
    exit 2 + stderr (hence the `>&2` redirect), the others read JSON on stdout.
    """
    spec = HOOK_SPECS[harness]
    base = f"{BYOR_COMMAND_SIGNATURE} {harness}"
    return f"{base} >&2" if spec.stderr_feedback else base


def global_hook_dir(harness: Harness, home: Path) -> Path:
    """The harness's global config directory under the user's home."""
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
        entry: dict[str, JsonValue] = {
            "hooks": [{"type": "command", "command": command}]
        }
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
        raise ConfigError(f"{relpath} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{relpath}: expected a JSON object at the top level")
    return data


def _save_config(path: Path, config: dict[str, JsonValue]) -> None:
    content = json.dumps(config, indent=2) + "\n"
    write_text_atomic(path, content)


def _entries(
    config: dict[str, JsonValue], spec: HookSpec, relpath: str
) -> list[JsonValue]:
    node: JsonValue = config
    for key in spec.key_path:
        if not isinstance(node, dict):
            raise ConfigError(f"{relpath}: expected '{key}' under a JSON object")
        child = node.get(key)
        if child is None:
            return []
        node = child
    if not isinstance(node, list):
        raise ConfigError(f"{relpath}: expected {'.'.join(spec.key_path)} to be a list")
    return node


def _set_entries(
    config: dict[str, JsonValue], spec: HookSpec, entries: list[JsonValue]
) -> None:
    """Write entries at the key path, pruning empty containers it leaves behind."""
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
    """True when every command in the entry is ours — the shape we install.

    An entry a user mixed their own commands into is user-edited and stays,
    matching the managed-marker rule for files.
    """
    commands = _entry_commands(entry)
    return bool(commands) and all(_is_byor_command(command) for command in commands)


def _contains_byor_command(entry: JsonValue) -> bool:
    return any(_is_byor_command(command) for command in _entry_commands(entry))


def _entry_commands(entry: JsonValue) -> Sequence[JsonValue]:
    """The command strings in an entry, across the nested and flat shapes."""
    if not isinstance(entry, dict):
        return []
    nested = entry.get("hooks")
    if isinstance(nested, list):
        return [hook.get("command") for hook in nested if isinstance(hook, dict)]
    return [entry.get("command")]


def _is_byor_command(command: JsonValue) -> bool:
    return isinstance(command, str) and BYOR_COMMAND_SIGNATURE in command
