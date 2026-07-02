"""Install BYOR into the user environment.

The install command prepares global rule discovery, records selected agents, and writes managed
integration files. It is intentionally separate from repository initialization because machine-level
setup and project setup change different state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.agents.install import AGENT_CHOICES, install_agent
from byor.commands.prompts import ask, numbers_to_choices, print_options
from byor.config import (
    GlobalConfig,
    global_rules_dir,
    load_global_config,
    save_global_config,
)
from byor.errors import ConfigError
from byor.io.output import write_line, write_lines
from byor.io.paths import global_config_dir
from byor.scaffold.sgconfig import ensure_home_sgconfig

if TYPE_CHECKING:
    import argparse

__all__ = ("run_install",)


def run_install(args: argparse.Namespace) -> int:
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    agents = _resolve_agents(args, config)
    # The harness-neutral rule-capture skill always installs.
    if "skill" not in agents:
        agents.append("skill")

    messages: list[str] = []
    sgconfig_message = ensure_home_sgconfig(global_rules_dir(config_dir, config))
    if sgconfig_message is not None:
        messages.append(f"Wrote ~/{sgconfig_message.split()[-1]}")
    for agent in agents:
        messages.extend(install_agent(agent))
        if agent not in config.agents:
            config.agents.append(agent)
    save_global_config(config_dir, config)

    write_lines(messages)
    write_line("byor is installed. Run `byor init` in a repo to add project rules.")
    write_line('In your agent, say "set up byor" to import preferences from your existing CLAUDE.md / AGENTS.md.')
    return 0


def _resolve_agents(args: argparse.Namespace, config: GlobalConfig) -> list[str]:
    if args.agents is not None:
        return _parse_agents(args.agents)
    if not args.non_interactive:
        return _prompt_agents(config.agents)
    return list(config.agents)


def _parse_agents(raw: str) -> list[str]:
    agents = list(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    unknown = [agent for agent in agents if agent not in AGENT_CHOICES]
    if unknown:
        msg = f"Unknown agents: {', '.join(unknown)}. Choose from: {', '.join(AGENT_CHOICES)}."
        raise ConfigError(msg)
    return agents


def _prompt_agents(default: list[str]) -> list[str]:
    print_options("AI integrations to install:", AGENT_CHOICES)
    answer = _default_agent_numbers(default)
    while True:
        raw = ask("Enter numbers separated by commas", default=answer)
        agents = numbers_to_choices(raw, AGENT_CHOICES)
        if agents is not None:
            return agents
        write_line(f"Please enter numbers between 1 and {len(AGENT_CHOICES)}.")


def _default_agent_numbers(default: list[str]) -> str:
    numbers = [str(AGENT_CHOICES.index(agent) + 1) for agent in default if agent in AGENT_CHOICES]
    return ",".join(numbers) if numbers else "1"
