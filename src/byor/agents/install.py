"""AI agent adapters: real post-edit hooks, plugin extensions, and the skill.

Agents are discoverable by their harness (the skill via the cross-agent
`SKILL.md` standard, diagnostics via the installed post-edit hook or plugin), so
byor writes no instruction files — the hook runs `byor agent-check`
automatically and the harness surfaces the skill on its own.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from byor.agents.harness import HARNESS_CHOICES, Harness
from byor.agents.hookconfig import hook_installed, install_hook, uninstall_hook
from byor.agents.opencode import (
    OPENCODE_MARKER,
    OPENCODE_PLUGIN,
    OPENCODE_PLUGIN_RELPATH,
)
from byor.agents.pi import PI_EXTENSION, PI_EXTENSION_RELPATH, PI_MARKER
from byor.config import load_global_config, save_global_config
from byor.io.fsio import MANAGED_MARKER, marked_text_status, write_marked_text
from byor.io.paths import global_config_dir
from byor.rules.skill import SKILL_MARKDOWN, global_skill_paths

# The four real-hook harnesses: a set for membership, a map for Harness lookup.
HOOK_HARNESSES: frozenset[str] = frozenset(HARNESS_CHOICES)
HARNESS_BY_NAME: dict[str, Harness] = {harness: harness for harness in HARNESS_CHOICES}


@dataclass(frozen=True)
class PluginAgent:
    """A harness whose integration is a single byor-managed plugin file."""

    relpath: str
    content: str
    marker: str


# Harnesses that hook via a TypeScript plugin/extension file rather than a JSON
# hook config; each is one managed file, written on install and verified by
# doctor. Both already read the skill from `.agents/skills/`, so neither needs
# skill-specific work.
PLUGIN_AGENTS: dict[str, PluginAgent] = {
    "opencode": PluginAgent(OPENCODE_PLUGIN_RELPATH, OPENCODE_PLUGIN, OPENCODE_MARKER),
    "pi": PluginAgent(PI_EXTENSION_RELPATH, PI_EXTENSION, PI_MARKER),
}

AGENT_CHOICES = (
    "claude-code",
    "codex",
    "copilot",
    "cursor",
    "opencode",
    "pi",
    "skill",
)

# Harnesses that need a one-time manual step before their hook will fire; printed
# after install so the user is not left wondering why nothing happens.
HARNESS_MANUAL_STEPS: dict[Harness, str] = {
    "codex": "Codex only runs trusted hooks: run `/hooks` in Codex to trust it.",
}


def run_hook(args: argparse.Namespace) -> int:
    """`byor hook install|uninstall --agent NAME` — global, no repo needed.

    The agent is recorded in the global config's ai.agents so doctor and
    uninstall know about it.
    """
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    if args.hook_action == "install":
        messages = install_agent(args.agent)
        recorded = args.agent not in config.agents
        if recorded:
            config.agents.append(args.agent)
    else:
        messages = uninstall_agent(args.agent)
        recorded = args.agent in config.agents
        if recorded:
            config.agents.remove(args.agent)
    if recorded:
        save_global_config(config_dir, config)
    for message in messages:
        print(message)
    return 0


def install_agents(agents: Sequence[str]) -> list[str]:
    """Install each requested agent's hook, plugin, or skill — all global."""
    messages: list[str] = []
    for agent in agents:
        messages.extend(install_agent(agent))
    return messages


def install_agent(agent: str) -> list[str]:
    """Install one agent adapter globally; returns summary lines for changes."""
    if agent == "skill":
        return _install_skill()
    plugin = PLUGIN_AGENTS.get(agent)
    if plugin is not None:
        return _install_plugin(plugin)
    harness = _as_harness(agent)
    if harness is None:
        return []
    messages = install_hook(harness)
    manual_step = HARNESS_MANUAL_STEPS.get(harness)
    if manual_step is not None and messages:
        messages.append(manual_step)
    return messages


def uninstall_agent(agent: str) -> list[str]:
    """Remove one agent adapter; only marker-bearing files are deleted."""
    if agent == "skill":
        messages: list[str] = []
        for path in global_skill_paths():
            messages.extend(_remove_managed_path(path, _home_display(path)))
        return messages
    plugin = PLUGIN_AGENTS.get(agent)
    if plugin is not None:
        path = _plugin_path(plugin)
        return _remove_managed_path(path, _home_display(path), plugin.marker)
    harness = _as_harness(agent)
    if harness is None:
        return []
    return uninstall_hook(harness)


def agent_file_problems(agents: Sequence[str]) -> list[str]:
    """Integration problems for doctor's agent_files check.

    Plugin files (OpenCode, Pi) are byor-managed; the other harnesses'
    integration is their global hook config, verified by checking a byor hook is
    present. The skill renders are not checked here — self-heal keeps them current
    on every byor command, so there is no drift for doctor to report.
    """
    problems: list[str] = []
    for agent in agents:
        if (plugin := PLUGIN_AGENTS.get(agent)) is not None:
            problems.extend(_plugin_problems(plugin))
        elif (harness := _as_harness(agent)) is not None and not _hook_present(harness):
            problems.append(f"the {agent} hook is not installed")
    return problems


def _hook_present(harness: Harness) -> bool:
    """Whether a byor hook is installed in the harness's global config."""
    return hook_installed(harness)


def _as_harness(agent: str) -> Harness | None:
    """The Harness for an agent that drives a real hook, else None."""
    return HARNESS_BY_NAME.get(agent)


def _install_skill() -> list[str]:
    """Write the byor-owned skill to its global discovery locations.

    Both renders are byor-managed copies of the packaged skill; an unmarked file
    a user placed at either path is left untouched, like any managed file.
    """
    messages: list[str] = []
    for path in global_skill_paths():
        messages.extend(_write_managed_path(path, SKILL_MARKDOWN, _home_display(path)))
    return messages


def _install_plugin(plugin: PluginAgent) -> list[str]:
    """Write a byor-managed plugin to its global location under the user's home."""
    path = _plugin_path(plugin)
    return _write_managed_path(path, plugin.content, _home_display(path), plugin.marker)


def _plugin_path(plugin: PluginAgent) -> Path:
    """The plugin's absolute path; its relpath is relative to the home directory."""
    return Path.home() / plugin.relpath


def _plugin_problems(plugin: PluginAgent) -> list[str]:
    """Same ownership rules as the skill renders: a drifted marker-bearing
    plugin needs a reinstall; an unmarked file is user-owned and accepted.
    """
    path = _plugin_path(plugin)
    display = _home_display(path)
    status = marked_text_status(path, plugin.content, plugin.marker)
    if status == "missing":
        return [f"{display} is missing"]
    if status == "drifted":
        return [f"{display} is out of date"]
    return []


def _write_managed_file(
    repo_root: Path, relpath: str, content: str, marker: str = MANAGED_MARKER
) -> list[str]:
    return _write_managed_path(repo_root / relpath, content, relpath, marker)


def _remove_managed_file(
    repo_root: Path, relpath: str, marker: str = MANAGED_MARKER
) -> list[str]:
    return _remove_managed_path(repo_root / relpath, relpath, marker)


def _write_managed_path(
    path: Path, content: str, display: str, marker: str = MANAGED_MARKER
) -> list[str]:
    result = write_marked_text(path, content, marker)
    if result == "unmarked":
        return [f"{display} exists without the BYOR marker; left untouched."]
    if result == "unchanged":
        return []
    return [f"Wrote {display}"]


def _remove_managed_path(
    path: Path, display: str, marker: str = MANAGED_MARKER
) -> list[str]:
    if not path.is_file():
        return []
    if marker not in path.read_text(encoding="utf-8"):
        return [f"{display} exists without the BYOR marker; left untouched."]
    path.unlink()
    return [f"Removed {display}"]


def _home_display(path: Path) -> str:
    """A `~/...` label for a global file under the user's home."""
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)
