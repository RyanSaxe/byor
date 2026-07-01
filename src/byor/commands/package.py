"""Package commands: list available packages and install one into a repository.

Packages mirror profiles: a named bundle in the global config a repo opts into.
Where a profile subtracts (exclusions), a package adds — its rules and checks
apply once installed, and stay upgradable from the global source via sync.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from byor.config import (
    GlobalConfig,
    global_packages_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    save_local_config,
)
from byor.errors import ConfigError
from byor.io.paths import global_config_dir, resolve_repo_root
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo


def run_package(args: argparse.Namespace) -> int:
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    if args.package_action == "list":
        _print_packages(config_dir, config)
        return 0
    if args.package_action == "add":
        repo_root = resolve_repo_root(explicit=args.repo)
        load_repo_config(repo_root)
        add_package_to_local(repo_root, config_dir, config, args.name)
        print(f"Installed package '{args.name}' in .byor/local.yml")
        _, result = sync_repo(repo_root, load_canonical_rules(config_dir))
        if result.changed:
            print(f"Synced {summarize_changes(result)} into {repo_root}")
        return 0
    raise ConfigError(f"unknown package action: {args.package_action}")


def add_package_to_local(
    repo_root: Path, config_dir: Path, global_config: GlobalConfig, name: str
) -> None:
    """Record a package opt-in in .byor/local.yml; a no-op when already installed."""
    _require_package(config_dir, global_config, name)
    local = load_local_config(repo_root)
    if name not in local.packages:
        local.packages.append(name)
        save_local_config(repo_root, local)


def available_packages(config_dir: Path, global_config: GlobalConfig) -> list[str]:
    """The package names that exist under the global packages directory."""
    base = global_packages_dir(config_dir, global_config)
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def _require_package(config_dir: Path, global_config: GlobalConfig, name: str) -> None:
    available = available_packages(config_dir, global_config)
    if name not in available:
        listed = ", ".join(available) or "none available"
        raise ConfigError(f"unknown package '{name}' (available: {listed})")


def _print_packages(config_dir: Path, global_config: GlobalConfig) -> None:
    packages = available_packages(config_dir, global_config)
    if not packages:
        print("No packages available.")
        return
    for name in packages:
        print(name)
