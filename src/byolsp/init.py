"""`byolsp init`: create the repository layout and wire up ast-grep (SPEC 15.1)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
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
    rule_dir_relpaths,
    save_global_config,
    save_local_config,
    save_repo_config,
    save_repo_registry,
)
from byolsp.errors import ConfigError, RepoNotInitialized
from byolsp.ignore import IgnoreMode, ignore_file, write_ignore_block
from byolsp.paths import global_config_dir, resolve_repo_root
from byolsp.sgconfig import ensure_rule_dirs
from byolsp.sync import load_canonical_rules, summarize_changes, sync_repo

GIT_HOOKS_NOTICE = (
    "Git hook shims are not implemented yet; rerun `byolsp init --git-hooks` "
    "after upgrading."
)


@dataclass
class InitOptions:
    """Fully resolved init choices; defaults live in _options_from_args."""

    agents: list[str]
    ignore_mode: IgnoreMode
    git_hooks: bool
    register: bool
    replace_sgconfig: bool


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
    sgconfig_message = ensure_rule_dirs(
        repo_root / repo_config.paths.sgconfig,
        rule_dir_relpaths(repo_config.paths),
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
    _, sync_result = sync_repo(repo_root, load_canonical_rules(config_dir))
    if sync_result.changed:
        messages.append(f"Synced {summarize_changes(sync_result)}")
    # Seam for the doctor component (SPEC 15.1 step 8): run `doctor --quick` here.
    return messages


def _bootstrap_global_dir(config_dir: Path) -> GlobalConfig:
    """Create the global dir, config, rules dir, and repo registry if missing."""
    if global_config_path(config_dir).is_file():
        config = load_global_config(config_dir)
    else:
        config = GlobalConfig()
        save_global_config(config_dir, config)
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
    for rules_dir in rule_dir_relpaths(config.paths):
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
    _print_options("AI integrations to install:", AGENT_CHOICES)
    while True:
        raw = _ask("Enter numbers separated by commas", default="1")
        agents = _numbers_to_choices(raw, AGENT_CHOICES)
        if agents is not None:
            return agents
        print(f"Please enter numbers between 1 and {len(AGENT_CHOICES)}.")


def _prompt_ignore_mode() -> IgnoreMode:
    choice = _prompt_choice(
        "Where should byolsp write its git ignore entries?",
        ("project .gitignore (team-visible)", "local .git/info/exclude (private)"),
    )
    return "local" if choice == 1 else "project"


def _prompt_git_hooks() -> bool:
    choice = _prompt_choice(
        "Install git hook shims that run `byolsp sync` after merge and checkout?",
        ("no", "yes"),
    )
    return choice == 1


def _prompt_choice(intro: str, options: Sequence[str]) -> int:
    """Ask a numbered single-choice question; returns the zero-based index."""
    _print_options(intro, options)
    while True:
        raw = _ask("Enter a number", default="1")
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"Please enter a number between 1 and {len(options)}.")


def _print_options(intro: str, options: Sequence[str]) -> None:
    print(intro)
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")


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
