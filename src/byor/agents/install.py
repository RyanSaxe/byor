"""Install and verify BYOR agent integrations.

The install layer coordinates hooks, plugin files, and the shared skill render while respecting
user-owned files. It is the global integration boundary used by install commands, doctor checks, and
self-healing upgrades.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from byor.agents.harness import HARNESS_CHOICES, Harness
from byor.agents.hookconfig import has_byor_hook, hook_problem, install_hook, uninstall_hook
from byor.agents.opencode import (
    OPENCODE_MARKER,
    OPENCODE_PLUGIN,
    OPENCODE_PLUGIN_RELPATH,
)
from byor.agents.pi import PI_EXTENSION, PI_EXTENSION_RELPATH, PI_MARKER
from byor.agents.skill import global_skill_dirs, skill_files
from byor.config import updating_machine_state
from byor.errors import ConfigError
from byor.io.fsio import MANAGED_MARKER, has_marker, marked_text_status, prune_empty_dirs, write_marked_text
from byor.io.output import write_lines
from byor.io.paths import global_state_dir

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

__all__ = (
    "PluginAgent",
    "UnrecordedAgents",
    "agent_file_problems",
    "install_agent",
    "run_hook",
    "uninstall_agent",
    "unrecorded_agents",
)

# The stdin-hook harnesses, mapped for Harness lookup by agent name.
HARNESS_BY_NAME: dict[str, Harness] = {harness: harness for harness in HARNESS_CHOICES}


@dataclass(frozen=True)
class PluginAgent:
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
    "antigravity",
    "opencode",
    "pi",
    "skill",
)

# Harnesses that need a one-time manual step before their hook will fire; printed
# after install so the user is not left wondering why nothing happens.
HARNESS_MANUAL_STEPS: dict[Harness, str] = {
    "codex": "Codex only runs trusted hooks: run `/hooks` in Codex to trust them (again after byor adds a new hook).",
}


def run_hook(args: argparse.Namespace) -> int:
    if args.hook_action == "install":
        messages = install_agent(args.agent)
        with updating_machine_state(global_state_dir()) as state:
            if args.agent not in state.agents:
                state.agents.append(args.agent)
    else:
        # Forget the agent before touching its files: if state.yml cannot be
        # written, nothing is removed, instead of hooks that self-heal would
        # silently reinstall from the stale record.
        with updating_machine_state(global_state_dir()) as state:
            if args.agent in state.agents:
                state.agents.remove(args.agent)
        messages = uninstall_agent(args.agent)
    write_lines(messages)
    return 0


def install_agent(agent: str) -> list[str]:
    if agent == "skill":
        return _install_skill()
    plugin = PLUGIN_AGENTS.get(agent)
    if plugin is not None:
        return _install_plugin(plugin)
    harness = HARNESS_BY_NAME.get(agent)
    if harness is None:
        return []
    messages = install_hook(harness)
    manual_step = HARNESS_MANUAL_STEPS.get(harness)
    if manual_step is not None and messages:
        messages.append(manual_step)
    return messages


def uninstall_agent(agent: str) -> list[str]:
    if agent == "skill":
        messages: list[str] = []
        for path in _global_skill_file_paths():
            messages.extend(_remove_managed_path(path, _home_display(path)))
        for base in global_skill_dirs():
            prune_empty_dirs(base, keep_root=False)
        return messages
    plugin = PLUGIN_AGENTS.get(agent)
    if plugin is not None:
        path = _plugin_path(plugin)
        return _remove_managed_path(path, _home_display(path), marker=plugin.marker)
    harness = HARNESS_BY_NAME.get(agent)
    if harness is None:
        return []
    return uninstall_hook(harness)


def agent_file_problems(agents: Sequence[str]) -> list[str]:
    """Integration problems for doctor's agent_files check, without writing.

    Plugin files (OpenCode, Pi) and the skill renders are byor-managed marked
    files, compared against what install would write; the other harnesses'
    integration is their global hook config, verified by checking a byor hook
    is present. Marked-file drift is usually short-lived — self-heal rewrites
    it on most byor commands — but doctor still reports whatever is on disk.
    """
    problems: list[str] = []
    for agent in agents:
        if agent == "skill":
            problems.extend(_skill_problems())
        elif (plugin := PLUGIN_AGENTS.get(agent)) is not None:
            problems.extend(_marked_file_problems(_plugin_path(plugin), plugin.content, marker=plugin.marker))
        elif (harness := HARNESS_BY_NAME.get(agent)) is not None:
            problem = hook_problem(harness)
            if problem is not None:
                problems.append(problem)
    return problems


class UnrecordedAgents(NamedTuple):
    found: list[str]
    problems: list[str]
    """One line per agent whose config could not be read, so the others are still checked."""


def unrecorded_agents(recorded: Sequence[str]) -> UnrecordedAgents:
    """Find agents that have byor's hooks or plugin file but are missing from `recorded`.

    Self-heal refreshes only recorded agents, so an integration installed by
    hand or by an older byor stays at whatever version wrote it. The skill
    counts too: `byor install` always records it, but a deleted `state.yml`
    loses that record like any other. Each agent is checked on
    its own: an unreadable config, such as a settings file that is not valid
    UTF-8 or JSON, becomes a problem line for that agent and never stops the
    scan or the command that runs it.
    """
    found: list[str] = []
    problems: list[str] = []
    for agent in AGENT_CHOICES:
        if agent in recorded:
            continue
        try:
            if _has_byor_integration(agent):
                found.append(agent)
        except ConfigError as error:
            problems.append(str(error))
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"cannot read byor's {agent} integration: {error}")
    return UnrecordedAgents(found=found, problems=problems)


def _has_byor_integration(agent: str) -> bool:
    if agent == "skill":
        return any(
            marked_text_status(base / relpath, content, marker=MANAGED_MARKER) in ("unchanged", "drifted")
            for base in global_skill_dirs()
            for relpath, content in skill_files()
        )
    if (plugin := PLUGIN_AGENTS.get(agent)) is not None:
        status = marked_text_status(_plugin_path(plugin), plugin.content, marker=plugin.marker)
        return status in ("unchanged", "drifted")
    harness = HARNESS_BY_NAME.get(agent)
    return harness is not None and has_byor_hook(harness)


def _install_skill() -> list[str]:
    messages: list[str] = []
    desired = skill_files()
    keep = {relpath for relpath, _ in desired}
    for base in global_skill_dirs():
        for relpath, content in desired:
            path = base / relpath
            messages.extend(_write_managed_path(path, content, display=_home_display(path)))
        messages.extend(_remove_stale_skill_files(base, keep=keep))
    return messages


# Delete byor-marked markdown the current render no longer ships: files a
# byor upgrade renamed or removed would otherwise linger forever, feeding
# agents stale guidance. Unmarked files are user-owned and stay.
def _remove_stale_skill_files(base: Path, *, keep: set[str]) -> list[str]:
    if not base.is_dir():
        return []
    messages: list[str] = []
    for path in sorted(base.rglob("*.md")):
        relpath = path.relative_to(base).as_posix()
        if relpath in keep or not has_marker(path, MANAGED_MARKER):
            continue
        path.unlink()
        messages.append(f"Removed stale {_home_display(path)}")
    prune_empty_dirs(base)
    return messages


def _global_skill_file_paths() -> list[Path]:
    return [base / relpath for base in global_skill_dirs() for relpath, _ in skill_files()]


def _install_plugin(plugin: PluginAgent) -> list[str]:
    path = _plugin_path(plugin)
    return _write_managed_path(
        path,
        plugin.content,
        display=_home_display(path),
        marker=plugin.marker,
    )


def _plugin_path(plugin: PluginAgent) -> Path:
    return Path.home() / plugin.relpath


def _skill_problems() -> list[str]:
    return [
        problem
        for base in global_skill_dirs()
        for relpath, content in skill_files()
        for problem in _marked_file_problems(base / relpath, content, marker=MANAGED_MARKER)
    ]


def _marked_file_problems(path: Path, content: str, *, marker: str) -> list[str]:
    display = _home_display(path)
    status = marked_text_status(path, content, marker=marker)
    if status == "missing":
        return [f"{display} is missing"]
    if status == "drifted":
        return [f"{display} is out of date"]
    return []


def _write_managed_path(
    path: Path,
    content: str,
    *,
    display: str,
    marker: str = MANAGED_MARKER,
) -> list[str]:
    try:
        result = write_marked_text(path, content, marker=marker)
    except OSError as error:
        # A read-only plugin or skill file is one agent's problem, which install
        # and self-heal report per agent instead of failing every command.
        msg = f"cannot write {display}: {error.strerror or error}"
        raise ConfigError(msg) from error
    if result == "unmarked":
        return [f"{display} exists without the BYOR marker; left untouched."]
    if result == "unchanged":
        return []
    return [f"Wrote {display}"]


def _remove_managed_path(path: Path, display: str, *, marker: str = MANAGED_MARKER) -> list[str]:
    if not path.is_file():
        return []
    if not has_marker(path, marker):
        return [f"{display} exists without the BYOR marker; left untouched."]
    path.unlink()
    return [f"Removed {display}"]


def _home_display(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)
