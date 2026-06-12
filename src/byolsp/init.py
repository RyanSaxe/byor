"""`byolsp init`: create the repository layout and wire up ast-grep (SPEC 15.1)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from byolsp.agents import AGENT_CHOICES, install_agent_instructions
from byolsp.config import (
    GlobalConfig,
    LocalConfig,
    RepoConfig,
    global_config_path,
    global_rules_dir,
    load_global_config,
    load_repo_config,
    local_config_path,
    register_repo,
    repo_config_path,
    repo_registry_path,
    save_global_config,
    save_local_config,
    save_repo_config,
    save_repo_registry,
)
from byolsp.errors import ConfigError, RepoNotInitialized
from byolsp.ignore import IgnoreMode, ignore_file, write_ignore_block
from byolsp.paths import global_config_dir, resolve_repo_root
from byolsp.sgconfig import ensure_rule_dirs

GIT_HOOKS_NOTICE = (
    "Git hook shims are not implemented yet; rerun `byolsp init --git-hooks` "
    "after upgrading."
)


@dataclass
class InitOptions:
    agents: list[str] = field(default_factory=list)
    ignore_mode: IgnoreMode = "project"
    git_hooks: bool = False
    register: bool = True
    replace_sgconfig: bool = False


def run_init(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    options = _options_from_args(args)
    for message in initialize_repo(repo_root, global_config_dir(), options):
        print(message)
    print(f"Initialized BYOLSP in {repo_root}")
    return 0


def initialize_repo(
    repo_root: Path, config_dir: Path, options: InitOptions
) -> list[str]:
    """Run init steps 1-7 (SPEC 15.1); returns summary lines for changes made."""
    messages: list[str] = []
    global_config = _bootstrap_global_dir(config_dir)
    repo_config = _ensure_repo_layout(repo_root, options.agents)
    rule_dirs = [
        repo_config.paths.project_rules,
        repo_config.paths.personal_local_rules,
        repo_config.paths.personal_global_rules,
    ]
    sgconfig_message = ensure_rule_dirs(
        repo_root / repo_config.paths.sgconfig,
        rule_dirs,
        replace=options.replace_sgconfig,
    )
    if sgconfig_message is not None:
        messages.append(sgconfig_message)
    if write_ignore_block(repo_root, options.ignore_mode):
        target = ignore_file(repo_root, options.ignore_mode).relative_to(repo_root)
        messages.append(f"Wrote ignore block to {target.as_posix()}")
    messages.extend(install_agent_instructions(repo_root, options.agents))
    if options.git_hooks:
        messages.append(GIT_HOOKS_NOTICE)
    if options.register and register_repo(
        repo_root, repo_registry_path(config_dir, global_config)
    ):
        messages.append("Registered repository for `byolsp sync --all`")
    # Seam for later components (SPEC 15.1 step 8): once the sync and doctor
    # commands exist, run a full sync here, then `doctor --quick`.
    return messages


def _bootstrap_global_dir(config_dir: Path) -> GlobalConfig:
    """Create the global dir, config, rules dir, and repo registry if missing."""
    if not global_config_path(config_dir).is_file():
        save_global_config(config_dir, GlobalConfig())
    config = load_global_config(config_dir)
    global_rules_dir(config_dir, config).mkdir(parents=True, exist_ok=True)
    registry_path = repo_registry_path(config_dir, config)
    if not registry_path.is_file():
        save_repo_registry(registry_path, [])
    return config


def _ensure_repo_layout(repo_root: Path, agents: list[str]) -> RepoConfig:
    """Create .byolsp/ config files and rule directories (SPEC 6)."""
    config = _load_or_default_repo_config(repo_root)
    new_agents = [agent for agent in agents if agent not in config.agents]
    config.agents.extend(new_agents)
    if new_agents or not repo_config_path(repo_root).is_file():
        save_repo_config(repo_root, config)
    if not local_config_path(repo_root).is_file():
        save_local_config(repo_root, LocalConfig())
    for rules_dir in (
        config.paths.project_rules,
        config.paths.personal_local_rules,
        config.paths.personal_global_rules,
    ):
        gitkeep = repo_root / rules_dir / ".gitkeep"
        gitkeep.parent.mkdir(parents=True, exist_ok=True)
        gitkeep.touch(exist_ok=True)
    return config


def _load_or_default_repo_config(repo_root: Path) -> RepoConfig:
    try:
        return load_repo_config(repo_root)
    except RepoNotInitialized:
        return RepoConfig()


def _options_from_args(args: argparse.Namespace) -> InitOptions:
    interactive = not args.non_interactive
    if args.agents is not None:
        agents = _parse_agents(args.agents)
    else:
        agents = _prompt_agents() if interactive else []
    if args.ignore_mode is not None:
        ignore_mode: IgnoreMode = args.ignore_mode
    else:
        ignore_mode = _prompt_ignore_mode() if interactive else "project"
    if args.git_hooks is not None:
        git_hooks: bool = args.git_hooks
    else:
        git_hooks = _prompt_git_hooks() if interactive else False
    return InitOptions(
        agents=agents,
        ignore_mode=ignore_mode,
        git_hooks=git_hooks,
        register=not args.no_register,
        replace_sgconfig=args.replace_sgconfig,
    )


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


def _prompt_agents() -> list[str]:
    print("AI integrations to install:")
    for number, name in enumerate(AGENT_CHOICES, start=1):
        print(f"  {number}. {name}")
    while True:
        raw = _ask("Enter numbers separated by commas", default="1")
        agents = _numbers_to_choices(raw, AGENT_CHOICES)
        if agents is not None:
            return agents
        print(f"Please enter numbers between 1 and {len(AGENT_CHOICES)}.")


def _prompt_ignore_mode() -> IgnoreMode:
    print("Where should byolsp write its git ignore entries?")
    print("  1. project .gitignore (team-visible)")
    print("  2. local .git/info/exclude (private)")
    while True:
        raw = _ask("Enter a number", default="1")
        if raw == "1":
            return "project"
        if raw == "2":
            return "local"
        print("Please enter 1 or 2.")


def _prompt_git_hooks() -> bool:
    print("Install git hook shims that run `byolsp sync` after merge and checkout?")
    print("  1. no")
    print("  2. yes")
    while True:
        raw = _ask("Enter a number", default="1")
        if raw == "1":
            return False
        if raw == "2":
            return True
        print("Please enter 1 or 2.")


def _ask(question: str, default: str) -> str:
    try:
        answer = input(f"{question} [{default}]: ").strip()
    except EOFError:
        return default
    return answer or default


def _numbers_to_choices(raw: str, choices: Sequence[str]) -> list[str] | None:
    picks: list[str] = []
    for part in raw.split(","):
        number = part.strip()
        if not number.isdigit() or not 1 <= int(number) <= len(choices):
            return None
        pick = choices[int(number) - 1]
        if pick not in picks:
            picks.append(pick)
    return picks
