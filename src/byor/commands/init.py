"""`byor init`: create the repository layout and wire up ast-grep."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from byor.commands.doctor import quick_doctor_problems
from byor.commands.profile import add_profile_to_local
from byor.commands.prompts import prompt_choice
from byor.config import (
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
from byor.errors import RepoNotInitialized
from byor.io.paths import display_path, global_config_dir, resolve_repo_root
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo
from byor.scaffold.githooks import install_git_shims
from byor.scaffold.ignore import (
    IgnoreMode,
    ignore_file,
    write_ignore_block,
    write_rule_visibility_file,
)
from byor.scaffold.sgconfig import ensure_rule_dirs


@dataclass
class InitOptions:
    """Fully resolved init choices; defaults live in _options_from_args."""

    ignore_mode: IgnoreMode
    git_hooks: bool
    register: bool
    replace_sgconfig: bool
    profile: str | None


def run_init(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    defaults = load_global_config(config_dir).init
    options = _options_from_args(args, defaults)
    for message in initialize_repo(repo_root, config_dir, options):
        print(message)
    print(f"Initialized BYOR in {repo_root}")
    return 0


def initialize_repo(
    repo_root: Path, config_dir: Path, options: InitOptions
) -> list[str]:
    """Run init steps 1-7; returns summary lines for changes made."""
    messages: list[str] = []
    global_config = _bootstrap_global_dir(config_dir)
    repo_config = _ensure_repo_layout(repo_root)
    sgconfig_message = ensure_rule_dirs(
        repo_root / repo_config.paths.sgconfig,
        rule_dir_relpaths(repo_config.paths),
        replace=options.replace_sgconfig,
    )
    if sgconfig_message is not None:
        messages.append(sgconfig_message)
    if write_ignore_block(repo_root, options.ignore_mode):
        target = display_path(ignore_file(repo_root, options.ignore_mode), repo_root)
        messages.append(f"Wrote ignore block to {target}")
    if options.git_hooks:
        messages.extend(install_git_shims(repo_root))
    if options.register and register_repo(
        repo_root, repo_registry_path(config_dir, global_config)
    ):
        messages.append("Registered repository for `byor sync --all`")
    if options.profile is not None:
        add_profile_to_local(repo_root, global_config, options.profile)
        messages.append(f"Added profile '{options.profile}' to .byor/local.yml")
    _, sync_result = sync_repo(repo_root, load_canonical_rules(config_dir))
    if sync_result.changed:
        messages.append(f"Synced {summarize_changes(sync_result)}")
    # Run doctor --quick, surfacing only the problems it finds.
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


def _ensure_repo_layout(repo_root: Path) -> RepoConfig:
    """Create .byor/ config files and rule directories."""
    config = _load_or_default_repo_config(repo_root)
    if not repo_config_path(repo_root).is_file():
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
        return RepoConfig(project_name=repo_root.name)


def _options_from_args(args: argparse.Namespace, defaults: InitDefaults) -> InitOptions:
    """Resolve init choices: explicit flag > global default > prompt/built-in.

    Global defaults seed interactive prompts and stand in as the
    answers under `--non-interactive`; an explicit flag always overrides both.
    """
    interactive = not args.non_interactive
    if args.ignore_mode is not None:
        ignore_mode: IgnoreMode = args.ignore_mode
    else:
        ignore_mode = _resolve_ignore_mode(defaults.ignore_mode, interactive)
    if args.git_hooks is not None:
        git_hooks: bool = args.git_hooks
    else:
        git_hooks = _resolve_git_hooks(defaults.git_hooks, interactive)
    return InitOptions(
        ignore_mode=ignore_mode,
        git_hooks=git_hooks,
        register=not args.no_register,
        replace_sgconfig=args.replace_sgconfig,
        profile=_resolve_profile(args, defaults),
    )


def _resolve_ignore_mode(default: str | None, interactive: bool) -> IgnoreMode:
    fallback: IgnoreMode = "local" if default == "local" else "project"
    return _prompt_ignore_mode(fallback) if interactive else fallback


def _resolve_git_hooks(default: bool | None, interactive: bool) -> bool:
    fallback = default if default is not None else False
    return _prompt_git_hooks(fallback) if interactive else fallback


def _resolve_profile(args: argparse.Namespace, defaults: InitDefaults) -> str | None:
    if args.no_profile:
        return None
    if args.profile is not None:
        return args.profile
    return defaults.profile


def _prompt_ignore_mode(default: IgnoreMode) -> IgnoreMode:
    choice = prompt_choice(
        "Where should byor write its git ignore entries?",
        ("project .gitignore (team-visible)", "local .git/info/exclude (private)"),
        default=1 if default == "local" else 0,
    )
    return "local" if choice == 1 else "project"


def _prompt_git_hooks(default: bool) -> bool:
    choice = prompt_choice(
        "Install git hook shims that run `byor sync` after merge and checkout?",
        ("no", "yes"),
        default=1 if default else 0,
    )
    return choice == 1
