"""Per-harness post-edit hook configs, one converge-only-ours pipeline.

Every harness stores its hooks as a JSON list of entries — at a harness-specific
key path inside a harness-specific file — where each entry carries a shell
command. byor owns the entries whose command runs `byor agent-check`, and
converges them to the current command without touching entries a user added,
exactly as the original claude-code settings logic did. `HookSpec` captures the
four edges that differ per harness (file location, key path, entry shape, and
command string); `install_hook`/`uninstall_hook` drive the shared logic.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from byor.agents.harness import Harness, JsonValue
from byor.errors import ConfigError
from byor.io.fsio import write_text_atomic

HookScope = Literal["project", "global", "local"]

HOOK_SCOPES: tuple[HookScope, ...] = ("project", "global")

# agent-check exits 2 with diagnostics, which claude-code reads as feedback;
# every other harness wants exit 0, so its guard ends with `|| true`.
BYOR_COMMAND_SIGNATURE = "byor agent-check --stdin-hook"


@dataclass(frozen=True)
class HookSpec:
    """The per-harness edges of the shared converge pipeline."""

    harness: Harness
    project_relpath: str
    """Repo-relative config path for project scope."""
    global_relpath: str
    """Path relative to the harness's global dir."""
    key_path: tuple[str, ...]
    """JSON pointer to the entry list inside the config object."""
    matcher: str | None
    """An entry's matcher value, or None for harnesses without matchers."""
    nests_command: bool
    """True when the command lives in a nested hooks[] list (claude/codex)."""
    local_relpath: str | None = None
    """Repo-relative path for local scope, or None for harnesses without one."""
    stderr_feedback: bool = False
    """True when the harness reads feedback from stderr + exit 2, not stdout JSON."""


HOOK_SPECS: dict[Harness, HookSpec] = {
    "claude-code": HookSpec(
        harness="claude-code",
        project_relpath=".claude/settings.json",
        global_relpath="settings.json",
        key_path=("hooks", "PostToolUse"),
        matcher="Write|Edit|MultiEdit|NotebookEdit",
        nests_command=True,
        local_relpath=".claude/settings.local.json",
        stderr_feedback=True,
    ),
    "codex": HookSpec(
        harness="codex",
        project_relpath=".codex/hooks.json",
        global_relpath="hooks.json",
        key_path=("hooks", "PostToolUse"),
        matcher="Edit|Write",
        nests_command=True,
    ),
    "copilot": HookSpec(
        harness="copilot",
        project_relpath=".github/hooks/byor.json",
        global_relpath="hooks/byor.json",
        key_path=("postToolUse",),
        matcher=None,
        nests_command=False,
    ),
    "cursor": HookSpec(
        harness="cursor",
        project_relpath=".cursor/hooks.json",
        global_relpath="hooks.json",
        key_path=("hooks", "postToolUse"),
        matcher=None,
        nests_command=False,
    ),
}


def install_hook(repo_root: Path, harness: Harness, scope: HookScope) -> list[str]:
    """Converge the harness's byor hook entry into its config."""
    spec = HOOK_SPECS[harness]
    path = _config_path(repo_root, spec, scope)
    relpath = _display_relpath(spec, scope)
    config = _load_config(path, relpath)
    entries = _entries(config, spec, relpath)
    current = _byor_entry(spec, scope)
    if current in entries:
        return []
    kept = [entry for entry in entries if not _is_byor_entry(entry)]
    if any(_contains_byor_command(entry) for entry in kept):
        return []
    _set_entries(config, spec, [*kept, current])
    _save_config(path, config)
    return [f"Installed a {harness} post-edit hook in {relpath}"]


def uninstall_hook(repo_root: Path, harness: Harness, scope: HookScope) -> list[str]:
    """Drop byor-owned entries from the config; user entries stay."""
    spec = HOOK_SPECS[harness]
    path = _config_path(repo_root, spec, scope)
    relpath = _display_relpath(spec, scope)
    if not path.is_file():
        return []
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


def hook_installed(repo_root: Path, harness: Harness, scope: HookScope) -> bool:
    """Whether a byor-owned entry is present in the harness config."""
    spec = HOOK_SPECS[harness]
    path = _config_path(repo_root, spec, scope)
    if not path.is_file():
        return False
    relpath = _display_relpath(spec, scope)
    entries = _entries(_load_config(path, relpath), spec, relpath)
    return any(_contains_byor_command(entry) for entry in entries)


def hook_command(harness: Harness, scope: HookScope) -> str:
    """The shell command an entry runs, guarded for shared project scope."""
    spec = HOOK_SPECS[harness]
    base = f"{BYOR_COMMAND_SIGNATURE} {harness}"
    if spec.stderr_feedback:
        # The harness reads exit 2 + stderr; the others read JSON on stdout.
        base = f"{base} >&2"
    # Only the committed, team-shared project config carries the teammate guard;
    # global and local configs are personal.
    if scope != "project":
        return base
    guard = "command -v byor >/dev/null 2>&1 &&"
    return f"{guard} {base} || true"


def supports_local_scope(harness: Harness) -> bool:
    """Whether the harness has a local-scope config (claude-code's only)."""
    return HOOK_SPECS[harness].local_relpath is not None


def installed_scopes(harness: Harness) -> tuple[HookScope, ...]:
    """Scopes a harness's hook may live in; uninstall sweeps all of them."""
    if supports_local_scope(harness):
        return ("project", "global", "local")
    return ("project", "global")


def global_hook_dir(harness: Harness, home: Path) -> Path:
    """The harness's global config directory under the user's home."""
    return home / _GLOBAL_DIRS[harness]


_GLOBAL_DIRS: dict[Harness, str] = {
    "claude-code": ".claude",
    "codex": ".codex",
    "copilot": ".copilot",
    "cursor": ".cursor",
}


def _config_path(repo_root: Path, spec: HookSpec, scope: HookScope) -> Path:
    if scope == "local":
        return repo_root / _local_relpath(spec)
    if scope == "global":
        return global_hook_dir(spec.harness, Path.home()) / spec.global_relpath
    return repo_root / spec.project_relpath


def _display_relpath(spec: HookSpec, scope: HookScope) -> str:
    if scope == "local":
        return _local_relpath(spec)
    if scope == "global":
        return f"~/{_GLOBAL_DIRS[spec.harness]}/{spec.global_relpath}"
    return spec.project_relpath


def _local_relpath(spec: HookSpec) -> str:
    if spec.local_relpath is None:
        raise ConfigError(f"{spec.harness} has no local-scope hook config")
    return spec.local_relpath


def _byor_entry(spec: HookSpec, scope: HookScope) -> dict[str, JsonValue]:
    command = hook_command(spec.harness, scope)
    if spec.nests_command:
        entry: dict[str, JsonValue] = {
            "hooks": [{"type": "command", "command": command}]
        }
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
    write_text_atomic(path, json.dumps(config, indent=2) + "\n")


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
