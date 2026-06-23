"""Profile commands and application."""

from __future__ import annotations

import argparse
from pathlib import Path

from byor.config import (
    GlobalConfig,
    LocalConfig,
    ProfileConfig,
    load_global_config,
    load_repo_config,
    save_local_config,
)
from byor.errors import ConfigError
from byor.io.paths import global_config_dir, resolve_repo_root
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo


def run_profile(args: argparse.Namespace) -> int:
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    if args.profile_action == "list":
        _print_profiles(config)
        return 0
    if args.profile_action == "apply":
        repo_root = resolve_repo_root(explicit=args.repo)
        load_repo_config(repo_root)
        apply_profile_to_local(repo_root, config, args.name)
        print(f"Applied profile '{args.name}' to .byor/local.yml")
        _, result = sync_repo(repo_root, load_canonical_rules(config_dir))
        if result.changed:
            print(f"Synced {summarize_changes(result)} into {repo_root}")
        return 0
    raise ConfigError(f"unknown profile action: {args.profile_action}")


def apply_profile_to_local(
    repo_root: Path, global_config: GlobalConfig, name: str
) -> None:
    profile = _profile(global_config, name)
    save_local_config(
        repo_root,
        LocalConfig(
            excluded_rule_ids=list(profile.excluded_rule_ids),
            excluded_rule_tags=list(profile.excluded_rule_tags),
            excluded_checks=list(profile.excluded_checks),
            excluded_check_tags=list(profile.excluded_check_tags),
        ),
    )


def _profile(global_config: GlobalConfig, name: str) -> ProfileConfig:
    profile = global_config.profiles.get(name)
    if profile is None:
        available = ", ".join(sorted(global_config.profiles)) or "none configured"
        raise ConfigError(f"unknown profile '{name}' (available: {available})")
    return profile


def _print_profiles(config: GlobalConfig) -> None:
    if not config.profiles:
        print("No profiles configured.")
        return
    width = max(len(name) for name in config.profiles)
    for name, profile in config.profiles.items():
        description = profile.description or ""
        print(f"{name:<{width}}  {description}".rstrip())
