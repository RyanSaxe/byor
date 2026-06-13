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
from byor.agents.hookconfig import (
    HookScope,
    hook_installed,
    install_hook,
    installed_scopes,
    supports_local_scope,
    uninstall_hook,
)
from byor.agents.opencode import (
    OPENCODE_MARKER,
    OPENCODE_PLUGIN,
    OPENCODE_PLUGIN_RELPATH,
)
from byor.agents.pi import PI_EXTENSION, PI_EXTENSION_RELPATH, PI_MARKER
from byor.config import load_repo_config, save_repo_config
from byor.errors import ConfigError
from byor.io.fsio import MANAGED_MARKER, marked_text_status, write_marked_text
from byor.io.paths import resolve_repo_root
from byor.rules.skill import SKILL_MARKDOWN, SKILL_RELPATHS

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
    """`byor hook install|uninstall --agent NAME [--hook-scope SCOPE]`.

    Installed agents are recorded in ai.agents so doctor and uninstall know
    about them.
    """
    repo_root = resolve_repo_root(explicit=args.repo)
    config = load_repo_config(repo_root)
    if args.hook_action == "install":
        harness = _as_harness(args.agent)
        if args.hook_scope == "local" and (
            harness is None or not supports_local_scope(harness)
        ):
            raise ConfigError("--hook-scope local is only supported for claude-code")
        messages = install_agent(repo_root, args.agent, args.hook_scope)
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


def install_agents(
    repo_root: Path, agents: Sequence[str], hook_scope: HookScope = "project"
) -> list[str]:
    """Init step 5: install each requested agent's hook, plugin, or skill."""
    messages: list[str] = []
    for agent in agents:
        messages.extend(install_agent(repo_root, agent, hook_scope))
    return messages


def install_agent(
    repo_root: Path, agent: str, hook_scope: HookScope = "project"
) -> list[str]:
    """Install one agent adapter; returns summary lines for changes made."""
    if agent == "skill":
        return _install_skill(repo_root)
    plugin = PLUGIN_AGENTS.get(agent)
    if plugin is not None:
        return _write_managed_file(
            repo_root, plugin.relpath, plugin.content, marker=plugin.marker
        )
    harness = _as_harness(agent)
    if harness is None:
        return []
    messages = install_hook(repo_root, harness, hook_scope)
    manual_step = HARNESS_MANUAL_STEPS.get(harness)
    if manual_step is not None and messages:
        messages.append(manual_step)
    return messages


def uninstall_agent(repo_root: Path, agent: str) -> list[str]:
    """Remove one agent adapter; only marker-bearing files are deleted."""
    if agent == "skill":
        messages: list[str] = []
        for relpath in SKILL_RELPATHS:
            messages.extend(_remove_managed_file(repo_root, relpath))
        return messages
    plugin = PLUGIN_AGENTS.get(agent)
    if plugin is not None:
        return _remove_managed_file(repo_root, plugin.relpath, marker=plugin.marker)
    harness = _as_harness(agent)
    if harness is None:
        return []
    messages = []
    for scope in installed_scopes(harness):
        messages.extend(uninstall_hook(repo_root, harness, scope))
    return messages


def agent_file_problems(repo_root: Path, agents: Sequence[str]) -> list[str]:
    """Integration problems for doctor's agent_files check.

    Plugin files (OpenCode, Pi) are byor-managed; the other harnesses'
    integration is their hook config, verified by checking a byor hook is present
    in one of its scopes. The skill renders are not checked here — self-heal keeps
    them current on every byor command, so there is no drift for doctor to report.
    """
    problems: list[str] = []
    for agent in agents:
        if (plugin := PLUGIN_AGENTS.get(agent)) is not None:
            problems.extend(_plugin_problems(repo_root, plugin))
        elif (harness := _as_harness(agent)) is not None and not _hook_present(
            repo_root, harness
        ):
            problems.append(f"the {agent} hook is not installed")
    return problems


def _hook_present(repo_root: Path, harness: Harness) -> bool:
    """Whether a byor hook is installed in any of the harness's scopes."""
    return any(
        hook_installed(repo_root, harness, scope) for scope in installed_scopes(harness)
    )


def _as_harness(agent: str) -> Harness | None:
    """The Harness for an agent that drives a real hook, else None."""
    return HARNESS_BY_NAME.get(agent)


def _install_skill(repo_root: Path) -> list[str]:
    """Write the byor-owned skill to every discovery location.

    Both renders are byor-managed copies of the packaged skill; an unmarked file
    a user placed at either path is left untouched, like any managed file.
    """
    messages: list[str] = []
    for relpath in SKILL_RELPATHS:
        messages.extend(_write_managed_file(repo_root, relpath, SKILL_MARKDOWN))
    return messages


def _plugin_problems(repo_root: Path, plugin: PluginAgent) -> list[str]:
    """Same ownership rules as the skill renders: a drifted marker-bearing
    plugin needs a reinstall; an unmarked file is user-owned and accepted.
    """
    status = marked_text_status(
        repo_root / plugin.relpath, plugin.content, plugin.marker
    )
    if status == "missing":
        return [f"{plugin.relpath} is missing"]
    if status == "drifted":
        return [f"{plugin.relpath} is out of date"]
    return []


def _write_managed_file(
    repo_root: Path, relpath: str, content: str, marker: str = MANAGED_MARKER
) -> list[str]:
    result = write_marked_text(repo_root / relpath, content, marker)
    if result == "unmarked":
        return [f"{relpath} exists without the BYOR marker; left untouched."]
    if result == "unchanged":
        return []
    return [f"Wrote {relpath}"]


def _remove_managed_file(
    repo_root: Path, relpath: str, marker: str = MANAGED_MARKER
) -> list[str]:
    path = repo_root / relpath
    if not path.is_file():
        return []
    if marker not in path.read_text(encoding="utf-8"):
        return [f"{relpath} exists without the BYOR marker; left untouched."]
    path.unlink()
    return [f"Removed {relpath}"]
