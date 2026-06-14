"""`byor install`: one-time, machine-level registration of byor's AI integrations.

Sets up the global ast-grep config (`~/sgconfig.yml` -> `~/.config/byor/rules`),
writes the rule-capture skill and the chosen harnesses' post-edit hooks/plugins
to their global locations, and records the chosen agents in the global config so
`doctor` can verify them and self-heal keeps them current.
"""

from __future__ import annotations

import argparse

from byor.agents.install import AGENT_CHOICES, install_agent
from byor.commands.prompts import ask, numbers_to_choices, print_options
from byor.config import (
    GlobalConfig,
    global_rules_dir,
    load_global_config,
    save_global_config,
)
from byor.errors import ConfigError
from byor.io.paths import global_config_dir
from byor.scaffold.sgconfig import ensure_home_sgconfig


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

    for message in messages:
        print(message)
    print("byor is installed. Run `byor init` in a repo to add project rules.")
    return 0


def _resolve_agents(args: argparse.Namespace, config: GlobalConfig) -> list[str]:
    """The agents to install: explicit flag, else prompt, else the recorded set."""
    if args.agents is not None:
        return _parse_agents(args.agents)
    if not args.non_interactive:
        return _prompt_agents(config.agents)
    return list(config.agents)


def _parse_agents(raw: str) -> list[str]:
    agents = list(
        dict.fromkeys(item.strip() for item in raw.split(",") if item.strip())
    )
    unknown = [agent for agent in agents if agent not in AGENT_CHOICES]
    if unknown:
        raise ConfigError(
            f"Unknown agents: {', '.join(unknown)}. "
            f"Choose from: {', '.join(AGENT_CHOICES)}."
        )
    return agents


def _prompt_agents(default: list[str]) -> list[str]:
    print_options("AI integrations to install:", AGENT_CHOICES)
    answer = _default_agent_numbers(default)
    while True:
        raw = ask("Enter numbers separated by commas", default=answer)
        agents = numbers_to_choices(raw, AGENT_CHOICES)
        if agents is not None:
            return agents
        print(f"Please enter numbers between 1 and {len(AGENT_CHOICES)}.")


def _default_agent_numbers(default: list[str]) -> str:
    """The comma-separated prompt default seeded from the recorded agents."""
    numbers = [
        str(AGENT_CHOICES.index(agent) + 1)
        for agent in default
        if agent in AGENT_CHOICES
    ]
    return ",".join(numbers) if numbers else "1"
