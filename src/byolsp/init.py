"""`byolsp init`: create the repository layout and wire up ast-grep (SPEC 15.1)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from byolsp.agents import AGENT_CHOICES, HOOK_HARNESSES, install_agents
from byolsp.config import (
    GlobalConfig,
    InitDefaults,
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
from byolsp.doctor import quick_doctor_problems
from byolsp.errors import ConfigError, RepoNotInitialized
from byolsp.githooks import install_git_shims
from byolsp.hookconfig import HOOK_SCOPES, HookScope
from byolsp.ignore import (
    IgnoreMode,
    ignore_file,
    write_ignore_block,
    write_rule_visibility_file,
)
from byolsp.paths import global_config_dir, resolve_repo_root
from byolsp.sgconfig import ensure_rule_dirs
from byolsp.sync import load_canonical_rules, summarize_changes, sync_repo


@dataclass
class InitOptions:
    """Fully resolved init choices; defaults live in _options_from_args."""

    agents: list[str]
    ignore_mode: IgnoreMode
    git_hooks: bool
    hook_scope: HookScope
    register: bool
    replace_sgconfig: bool


def run_init(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    defaults = load_global_config(config_dir).init
    options = _options_from_args(args, defaults)
    for message in initialize_repo(repo_root, config_dir, options):
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
    messages.extend(install_agents(repo_root, options.agents, options.hook_scope))
    if options.git_hooks:
        messages.extend(install_git_shims(repo_root))
    if options.register and register_repo(
        repo_root, repo_registry_path(config_dir, global_config)
    ):
        messages.append("Registered repository for `byolsp sync --all`")
    _, sync_result = sync_repo(repo_root, load_canonical_rules(config_dir))
    if sync_result.changed:
        messages.append(f"Synced {summarize_changes(sync_result)}")
    # SPEC 15.1 step 8: doctor --quick, surfacing only the problems it finds.
    messages.extend(quick_doctor_problems(repo_root, config_dir))
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
    for personal_dir in (
        config.paths.personal_local_rules,
        config.paths.personal_global_rules,
    ):
        write_rule_visibility_file(repo_root / personal_dir)
    return config


def _load_or_default_repo_config(repo_root: Path) -> RepoConfig:
    try:
        return load_repo_config(repo_root)
    except RepoNotInitialized:
        return RepoConfig()


def _options_from_args(args: argparse.Namespace, defaults: InitDefaults) -> InitOptions:
    """Resolve init choices: explicit flag > global default > prompt/built-in.

    Global defaults (SPEC 28.5) seed interactive prompts and stand in as the
    answers under `--non-interactive`; an explicit flag always overrides both.
    """
    interactive = not args.non_interactive
    if args.agents is not None:
        agents = _parse_agents(args.agents)
    elif interactive:
        agents = _prompt_agents(defaults.agents)
    else:
        agents = list(defaults.agents) if defaults.agents is not None else []
    if args.ignore_mode is not None:
        ignore_mode: IgnoreMode = args.ignore_mode
    else:
        ignore_mode = _resolve_ignore_mode(defaults.ignore_mode, interactive)
    if args.git_hooks is not None:
        git_hooks: bool = args.git_hooks
    else:
        git_hooks = _resolve_git_hooks(defaults.git_hooks, interactive)
    hook_scope = _resolve_hook_scope(args, agents, interactive, defaults.hook_scope)
    # The harness-neutral rule-capture skill installs by default (SPEC 27.2).
    if "skill" not in agents:
        agents.append("skill")
    return InitOptions(
        agents=agents,
        ignore_mode=ignore_mode,
        git_hooks=git_hooks,
        hook_scope=hook_scope,
        register=not args.no_register,
        replace_sgconfig=args.replace_sgconfig,
    )


def _resolve_ignore_mode(default: str | None, interactive: bool) -> IgnoreMode:
    fallback: IgnoreMode = "local" if default == "local" else "project"
    return _prompt_ignore_mode(fallback) if interactive else fallback


def _resolve_git_hooks(default: bool | None, interactive: bool) -> bool:
    fallback = default if default is not None else False
    return _prompt_git_hooks(fallback) if interactive else fallback


def _resolve_hook_scope(
    args: argparse.Namespace,
    agents: list[str],
    interactive: bool,
    default: str | None,
) -> HookScope:
    """Ask hook scope once for all selected hook-capable agents (SPEC 28.3)."""
    if args.hook_scope is not None:
        return args.hook_scope
    fallback: HookScope = "global" if default == "global" else "project"
    if interactive and any(agent in HOOK_HARNESSES for agent in agents):
        return _prompt_hook_scope(fallback)
    return fallback


def _prompt_hook_scope(default: HookScope) -> HookScope:
    choice = _prompt_choice(
        "Where should byolsp register agent hooks?",
        ("project (committed, shared with the team)", "global (~/, personal)"),
        default=HOOK_SCOPES.index(default),
    )
    return HOOK_SCOPES[choice]


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


def _prompt_agents(default: list[str] | None) -> list[str]:
    _print_options("AI integrations to install:", AGENT_CHOICES)
    answer = _default_agent_numbers(default)
    while True:
        raw = _ask("Enter numbers separated by commas", default=answer)
        agents = _numbers_to_choices(raw, AGENT_CHOICES)
        if agents is not None:
            return agents
        print(f"Please enter numbers between 1 and {len(AGENT_CHOICES)}.")


def _default_agent_numbers(default: list[str] | None) -> str:
    """The comma-separated prompt default seeded from the global agents list."""
    if not default:
        return "1"
    numbers = [
        str(AGENT_CHOICES.index(agent) + 1)
        for agent in default
        if agent in AGENT_CHOICES
    ]
    return ",".join(numbers) if numbers else "1"


def _prompt_ignore_mode(default: IgnoreMode) -> IgnoreMode:
    choice = _prompt_choice(
        "Where should byolsp write its git ignore entries?",
        ("project .gitignore (team-visible)", "local .git/info/exclude (private)"),
        default=1 if default == "local" else 0,
    )
    return "local" if choice == 1 else "project"


def _prompt_git_hooks(default: bool) -> bool:
    choice = _prompt_choice(
        "Install git hook shims that run `byolsp sync` after merge and checkout?",
        ("no", "yes"),
        default=1 if default else 0,
    )
    return choice == 1


def _prompt_choice(intro: str, options: Sequence[str], default: int = 0) -> int:
    """Ask a numbered single-choice question; returns the zero-based index."""
    _print_options(intro, options)
    while True:
        raw = _ask("Enter a number", default=str(default + 1))
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
