"""`byor init`: create the repository layout and wire up ast-grep."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from byor.commands.doctor import quick_doctor_problems
from byor.commands.gate import install_gate
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
from byor.io.gitio import git_output
from byor.io.paths import display_path, global_config_dir, resolve_repo_root
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo
from byor.scaffold.githooks import install_git_shims
from byor.scaffold.ignore import (
    ignore_file,
    write_ignore_block,
    write_rule_visibility_file,
)
from byor.scaffold.sgconfig import ensure_rule_dirs


@dataclass
class InitOptions:
    """Fully resolved init choices; defaults live in _options_from_args."""

    private: bool
    git_hooks: bool
    gate: bool
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
    repo_config = _ensure_repo_layout(repo_root, options.private)
    sgconfig_message = ensure_rule_dirs(
        repo_root / repo_config.paths.sgconfig,
        rule_dir_relpaths(repo_config.paths),
        replace=options.replace_sgconfig,
    )
    if sgconfig_message is not None:
        messages.append(sgconfig_message)
    if write_ignore_block(repo_root, options.private):
        target = display_path(ignore_file(repo_root, options.private), repo_root)
        messages.append(f"Wrote ignore block to {target}")
    sgconfig = repo_config.paths.sgconfig
    if options.private and git_output(repo_root, "ls-files", "--", sgconfig):
        messages.append(
            f"warning: {sgconfig} is already tracked; git will still show byor's "
            "changes to it despite private mode"
        )
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
    if options.gate:
        messages.extend(install_gate(repo_root, config_dir, options.private))
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


def _ensure_repo_layout(repo_root: Path, private: bool) -> RepoConfig:
    """Create .byor/ config files and rule directories.

    A private setup git-ignores the whole `.byor/` tree, so every rule
    directory — the shared project one included — needs a visibility file to
    stay loadable by ast-grep; a shared setup only needs it on the personal ones.
    """
    config = _load_or_default_repo_config(repo_root)
    if not repo_config_path(repo_root).is_file():
        save_repo_config(repo_root, config)
    if not local_config_path(repo_root).is_file():
        save_local_config(repo_root, LocalConfig())
    for rules_dir in rule_dir_relpaths(config.paths):
        gitkeep = repo_root / rules_dir / ".gitkeep"
        gitkeep.parent.mkdir(parents=True, exist_ok=True)
        gitkeep.touch(exist_ok=True)
    visible_dirs = [
        config.paths.personal_local_rules,
        config.paths.personal_global_rules,
        config.paths.personal_packages_rules,
    ]
    if private:
        visible_dirs.append(config.paths.project_rules)
    for rules_dir in visible_dirs:
        write_rule_visibility_file(repo_root / rules_dir)
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
    if args.private is not None:
        private: bool = args.private
    else:
        private = _resolve_private(defaults.private, interactive)
    if args.git_hooks is not None:
        git_hooks: bool = args.git_hooks
    else:
        git_hooks = _resolve_git_hooks(defaults.git_hooks, interactive)
    if args.gate is not None:
        gate: bool = args.gate
    else:
        gate = _resolve_gate(defaults.gate, interactive)
    return InitOptions(
        private=private,
        git_hooks=git_hooks,
        gate=gate,
        register=not args.no_register,
        replace_sgconfig=args.replace_sgconfig,
        profile=_resolve_profile(args, defaults),
    )


def _resolve_private(default: bool | None, interactive: bool) -> bool:
    fallback = default if default is not None else False
    return _prompt_private(fallback) if interactive else fallback


def _resolve_gate(default: bool | None, interactive: bool) -> bool:
    fallback = default if default is not None else False
    return _prompt_gate(fallback) if interactive else fallback


def _resolve_git_hooks(default: bool | None, interactive: bool) -> bool:
    fallback = default if default is not None else False
    return _prompt_git_hooks(fallback) if interactive else fallback


def _resolve_profile(args: argparse.Namespace, defaults: InitDefaults) -> str | None:
    if args.no_profile:
        return None
    if args.profile is not None:
        return args.profile
    return defaults.profile


def _prompt_private(default: bool) -> bool:
    choice = prompt_choice(
        "Make this byor setup private (hide everything from git, don't commit)?",
        ("no, share it with the team", "yes, keep it to myself"),
        default=1 if default else 0,
    )
    return choice == 1


def _prompt_gate(default: bool) -> bool:
    choice = prompt_choice(
        "Install a blocking gate (pre-commit + CI) that enforces these rules?",
        ("no", "yes"),
        default=1 if default else 0,
    )
    return choice == 1


def _prompt_git_hooks(default: bool) -> bool:
    choice = prompt_choice(
        "Install git hook shims that run `byor sync` after merge and checkout?",
        ("no", "yes"),
        default=1 if default else 0,
    )
    return choice == 1
