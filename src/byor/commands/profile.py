"""Apply named BYOR exclusion profiles.

Profiles are global presets for excluding groups of rules or checks in a repository. This command
records those exclusions locally and resyncs the repository so profile changes affect the same
mirrors as manual excludes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.config import (
    GlobalConfig,
    ProfileConfig,
    load_global_config,
    load_local_config,
    load_repo_config,
    save_local_config,
)
from byor.errors import ConfigError
from byor.io.output import write_line
from byor.io.paths import global_config_dir, resolve_repo_root
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

__all__ = (
    "add_profile_to_local",
    "run_profile",
)


def run_profile(args: argparse.Namespace) -> int:
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    if args.profile_action == "list":
        _print_profiles(config)
        return 0
    if args.profile_action == "add":
        repo_root = resolve_repo_root(explicit=args.repo)
        load_repo_config(repo_root)
        add_profile_to_local(repo_root, config, name=args.name)
        write_line(f"Added profile '{args.name}' to .byor/local.yml")
        _, result = sync_repo(repo_root, load_canonical_rules(config_dir))
        if result.changed:
            write_line(f"Synced {summarize_changes(result)} into {repo_root}")
        return 0
    msg = f"unknown profile action: {args.profile_action}"
    raise ConfigError(msg)


def add_profile_to_local(
    repo_root: Path,
    global_config: GlobalConfig,
    *,
    name: str,
) -> None:
    profile = _profile(global_config, name)
    local = load_local_config(repo_root)
    _extend_unique(local.excluded_rule_ids, profile.excluded_rule_ids)
    _extend_unique(local.excluded_rule_tags, profile.excluded_rule_tags)
    _extend_unique(local.excluded_checks, profile.excluded_checks)
    _extend_unique(local.excluded_check_tags, profile.excluded_check_tags)
    save_local_config(repo_root, local)


def _extend_unique(target: list[str], additions: list[str]) -> None:
    for value in additions:
        if value not in target:
            target.append(value)


def _profile(global_config: GlobalConfig, name: str) -> ProfileConfig:
    profile = global_config.profiles.get(name)
    if profile is None:
        available = ", ".join(sorted(global_config.profiles)) or "none configured"
        msg = f"unknown profile '{name}' (available: {available})"
        raise ConfigError(msg)
    return profile


def _print_profiles(config: GlobalConfig) -> None:
    if not config.profiles:
        write_line("No profiles configured.")
        return
    width = max(len(name) for name in config.profiles)
    for name, profile in config.profiles.items():
        description = profile.description or ""
        write_line(f"{name:<{width}}  {description}".rstrip())
