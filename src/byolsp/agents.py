"""AI agent adapters: instruction files and real hooks (SPEC 15.10, 16)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TypeAlias

from byolsp.config import load_repo_config, save_repo_config
from byolsp.errors import ConfigError
from byolsp.fsio import write_marked_text, write_text_atomic
from byolsp.paths import resolve_repo_root

JsonValue: TypeAlias = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)

AGENT_CHOICES = ("generic", "claude-code", "codex", "copilot")

MANAGED_MARKER = "<!-- Managed by BYOLSP. Manual edits may be overwritten. -->"

AGENT_INSTRUCTIONS_RELPATH = ".byolsp/agents/README.md"

CLAUDE_SETTINGS_RELPATH = ".claude/settings.json"

CLAUDE_HOOK_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"

# Claude Code pipes tool-call JSON to PostToolUse hooks on stdin; on exit 2 it
# feeds the hook's stderr back to the model, hence the >&2 (agent-check exits 2
# exactly when it has diagnostics). The edited path stays quoted (SPEC 19).
CLAUDE_HOOK_COMMAND = (
    "f=$(python3 -c 'import json,sys; "
    'print(json.load(sys.stdin).get("tool_input", {}).get("file_path") or "")\'); '
    '[ -z "$f" ] || byolsp agent-check --files "$f" >&2'
)

CORE_INSTRUCTION = """\
This repository uses BYOLSP to expose custom ast-grep diagnostics.

After writing or editing code, run:

```bash
byolsp agent-check --files <changed files>
```

If BYOLSP reports a diagnostic, fix it before continuing.

If a rule says an exception is allowed with a comment, only keep the violating
code when the code is genuinely necessary and add a concise comment explaining why.
"""

GENERIC_AGENT_INSTRUCTIONS = (
    f"{MANAGED_MARKER}\n\n# BYOLSP Agent Instructions\n\n{CORE_INSTRUCTION}"
)


def run_hook(args: argparse.Namespace) -> int:
    """`byolsp hook install|uninstall --agent NAME` (SPEC 15.10).

    Installed agents are recorded in ai.agents so doctor and uninstall know
    about them (SPEC 10.1).
    """
    repo_root = resolve_repo_root(explicit=args.repo)
    config = load_repo_config(repo_root)
    if args.hook_action == "install":
        messages = install_agent(repo_root, args.agent)
        recorded = args.agent not in config.agents
        if recorded:
            config.agents.append(args.agent)
    else:
        messages = uninstall_agent(repo_root, args.agent)
        recorded = args.agent in config.agents
        if recorded:
            config.agents.remove(args.agent)
    if recorded:
        save_repo_config(repo_root, config)
    for message in messages:
        print(message)
    return 0


def install_agents(repo_root: Path, agents: Sequence[str]) -> list[str]:
    """Init step 5: the generic README is part of the repository layout (SPEC 6);
    the explicitly requested agents get their adapters on top.
    """
    messages = install_agent(repo_root, "generic")
    for agent in agents:
        if agent != "generic":
            messages.extend(install_agent(repo_root, agent))
    return messages


def install_agent(repo_root: Path, agent: str) -> list[str]:
    """Install one agent adapter; returns summary lines for changes made."""
    if agent == "claude-code":
        return _install_claude_code(repo_root)
    return _write_managed_file(
        repo_root, _instructions_relpath(agent), _agent_instructions(agent)
    )


def uninstall_agent(repo_root: Path, agent: str) -> list[str]:
    """Remove one agent adapter; only marker-bearing files are deleted (SPEC 17)."""
    messages: list[str] = []
    if agent == "claude-code":
        messages.extend(_remove_claude_code_hook(repo_root))
    messages.extend(_remove_managed_file(repo_root, _instructions_relpath(agent)))
    return messages


def missing_agent_files(repo_root: Path, agents: Sequence[str]) -> list[str]:
    """Expected-but-missing integration files for doctor's agent_files check."""
    missing: list[str] = []
    for agent in agents:
        if agent == "claude-code" and _claude_code_installed(repo_root):
            continue
        relpath = _instructions_relpath(agent)
        if not (repo_root / relpath).is_file():
            missing.append(relpath)
    return missing


def _instructions_relpath(agent: str) -> str:
    if agent == "generic":
        return AGENT_INSTRUCTIONS_RELPATH
    return f".byolsp/agents/{agent}.md"


def _agent_instructions(agent: str) -> str:
    if agent == "generic":
        return GENERIC_AGENT_INSTRUCTIONS
    notes = {
        "codex": (
            "Codex reads repository guidance from `AGENTS.md`. Copy the\n"
            "instruction above into `AGENTS.md` so Codex checks its changes\n"
            "automatically."
        ),
        "copilot": (
            "GitHub Copilot reads repository guidance from\n"
            "`.github/copilot-instructions.md`. Copy the instruction above into\n"
            "that file so Copilot checks its changes automatically."
        ),
    }
    return _instruction_file(f"BYOLSP {agent.title()} Instructions", notes[agent])


def _instruction_file(title: str, wiring_note: str) -> str:
    return f"{MANAGED_MARKER}\n\n# {title}\n\n{CORE_INSTRUCTION}\n{wiring_note}\n"


def _claude_code_instructions() -> str:
    wiring = json.dumps({"hooks": {"PostToolUse": [_claude_hook_group()]}}, indent=2)
    note = (
        "To check changes automatically, merge this PostToolUse hook into\n"
        f"`{CLAUDE_SETTINGS_RELPATH}` (or rerun "
        "`byolsp hook install --agent claude-code`\n"
        "once `.claude/` exists):\n"
        "\n"
        "```json\n"
        f"{wiring}\n"
        "```"
    )
    return _instruction_file("BYOLSP Claude Code Instructions", note)


def _write_managed_file(repo_root: Path, relpath: str, content: str) -> list[str]:
    result = write_marked_text(repo_root / relpath, content, MANAGED_MARKER)
    if result == "unmarked":
        return [f"{relpath} exists without the BYOLSP marker; left untouched."]
    if result == "unchanged":
        return []
    return [f"Wrote {relpath}"]


def _remove_managed_file(repo_root: Path, relpath: str) -> list[str]:
    path = repo_root / relpath
    if not path.is_file():
        return []
    if MANAGED_MARKER not in path.read_text(encoding="utf-8"):
        return [f"{relpath} exists without the BYOLSP marker; left untouched."]
    path.unlink()
    return [f"Removed {relpath}"]


def _install_claude_code(repo_root: Path) -> list[str]:
    """A real PostToolUse hook when Claude Code is detectable, else instructions."""
    if not _claude_code_detected(repo_root):
        return _write_managed_file(
            repo_root, _instructions_relpath("claude-code"), _claude_code_instructions()
        )
    settings_path = repo_root / CLAUDE_SETTINGS_RELPATH
    settings = _load_claude_settings(settings_path)
    groups = _post_tool_use_groups(settings)
    current = _claude_hook_group()
    if current in groups:
        return []
    # Converge byolsp-owned groups to the current hook (SPEC 17); a group the
    # user mixed their own hooks into is user-edited and stays as is.
    kept = [group for group in groups if not _is_byolsp_group(group)]
    if any(_contains_byolsp_command(group) for group in kept):
        return []
    _set_post_tool_use_groups(settings, [*kept, current])
    _save_claude_settings(settings_path, settings)
    return [f"Installed a PostToolUse hook in {CLAUDE_SETTINGS_RELPATH}"]


def _claude_code_detected(repo_root: Path) -> bool:
    return (repo_root / ".claude").is_dir()


def _claude_code_installed(repo_root: Path) -> bool:
    """Either adapter form counts: the instruction file or the settings hook."""
    if (repo_root / _instructions_relpath("claude-code")).is_file():
        return True
    settings_path = repo_root / CLAUDE_SETTINGS_RELPATH
    if not settings_path.is_file():
        return False
    try:
        settings = _load_claude_settings(settings_path)
        groups = _post_tool_use_groups(settings)
    except ConfigError:
        return False
    return any(_contains_byolsp_command(group) for group in groups)


def _remove_claude_code_hook(repo_root: Path) -> list[str]:
    """Drop the PostToolUse groups byolsp installed; user-edited groups stay."""
    settings_path = repo_root / CLAUDE_SETTINGS_RELPATH
    if not settings_path.is_file():
        return []
    settings = _load_claude_settings(settings_path)
    groups = _post_tool_use_groups(settings)
    kept = [group for group in groups if not _is_byolsp_group(group)]
    if len(kept) == len(groups):
        return []
    _set_post_tool_use_groups(settings, kept)
    _save_claude_settings(settings_path, settings)
    return [f"Removed the BYOLSP PostToolUse hook from {CLAUDE_SETTINGS_RELPATH}"]


def _is_byolsp_group(group: JsonValue) -> bool:
    """True for a matcher group whose every command is ours: the shape we install.

    A group where a user mixed in their own hooks counts as user-edited and is
    preserved, matching the managed-marker rule for files.
    """
    hooks = _group_hooks(group)
    return bool(hooks) and all(_is_byolsp_command(hook) for hook in hooks)


def _claude_hook_group() -> dict[str, JsonValue]:
    return {
        "matcher": CLAUDE_HOOK_MATCHER,
        "hooks": [{"type": "command", "command": CLAUDE_HOOK_COMMAND}],
    }


def _load_claude_settings(path: Path) -> dict[str, JsonValue]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"{CLAUDE_SETTINGS_RELPATH} is not valid JSON: {error}"
        ) from error
    if not isinstance(data, dict):
        raise ConfigError(
            f"{CLAUDE_SETTINGS_RELPATH}: expected a JSON object at the top level"
        )
    return data


def _save_claude_settings(path: Path, settings: dict[str, JsonValue]) -> None:
    write_text_atomic(path, json.dumps(settings, indent=2) + "\n")


def _post_tool_use_groups(settings: dict[str, JsonValue]) -> list[JsonValue]:
    """The hooks.PostToolUse list, [] when absent; raises on malformed types."""
    hooks = settings.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, dict):
        raise ConfigError(
            f"{CLAUDE_SETTINGS_RELPATH}: expected 'hooks' to be an object"
        )
    groups = hooks.get("PostToolUse")
    if groups is None:
        return []
    if not isinstance(groups, list):
        raise ConfigError(
            f"{CLAUDE_SETTINGS_RELPATH}: expected hooks.PostToolUse to be a list"
        )
    return groups


def _set_post_tool_use_groups(
    settings: dict[str, JsonValue], groups: list[JsonValue]
) -> None:
    """Replace hooks.PostToolUse, dropping empty containers it leaves behind."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    if groups:
        hooks["PostToolUse"] = groups
    else:
        hooks.pop("PostToolUse", None)
        if not hooks:
            del settings["hooks"]


def _group_hooks(group: JsonValue) -> list[JsonValue]:
    """The group's hooks list, or [] when the group is not shaped like one."""
    if not isinstance(group, dict):
        return []
    hooks = group.get("hooks")
    return hooks if isinstance(hooks, list) else []


def _contains_byolsp_command(group: JsonValue) -> bool:
    return any(_is_byolsp_command(hook) for hook in _group_hooks(group))


def _is_byolsp_command(hook: JsonValue) -> bool:
    if not isinstance(hook, dict):
        return False
    command = hook.get("command")
    return isinstance(command, str) and "byolsp agent-check" in command
