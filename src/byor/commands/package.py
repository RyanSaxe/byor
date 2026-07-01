"""Manage opt-in BYOR rule packages.

Packages are reusable bundles that repositories choose explicitly rather than inheriting globally.
This command lists available packages, records selected packages locally, and triggers sync so their
rules and checks become effective.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.config import (
    GlobalConfig,
    global_packages_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    save_local_config,
)
from byor.errors import ConfigError
from byor.io.output import write_line, write_lines
from byor.io.paths import global_config_dir, resolve_repo_root
from byor.rules.sync import load_canonical_rules, sync_repo

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

__all__ = (
    "add_package_to_local",
    "available_packages",
    "run_package",
)


def run_package(args: argparse.Namespace) -> int:
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    if args.package_action == "list":
        _print_packages(config_dir, config)
        return 0
    if args.package_action == "add":
        repo_root = resolve_repo_root(explicit=args.repo)
        load_repo_config(repo_root)
        add_package_to_local(
            repo_root,
            config_dir,
            global_config=config,
            name=args.name,
        )
        write_line(f"Installed package '{args.name}' in .byor/local.yml")
        _, result = sync_repo(repo_root, load_canonical_rules(config_dir))
        if result.changed:
            write_line(f"Synced package '{args.name}' into {repo_root}")
        return 0
    msg = f"unknown package action: {args.package_action}"
    raise ConfigError(msg)


def add_package_to_local(
    repo_root: Path,
    config_dir: Path,
    *,
    global_config: GlobalConfig,
    name: str,
) -> None:
    _require_package(config_dir, global_config, name=name)
    local = load_local_config(repo_root)
    if name not in local.packages:
        local.packages.append(name)
        save_local_config(repo_root, local)


def available_packages(config_dir: Path, global_config: GlobalConfig) -> list[str]:
    base = global_packages_dir(config_dir, global_config)
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def _require_package(config_dir: Path, global_config: GlobalConfig, *, name: str) -> None:
    available = available_packages(config_dir, global_config)
    if name not in available:
        listed = ", ".join(available) or "none available"
        msg = f"unknown package '{name}' (available: {listed})"
        raise ConfigError(msg)


def _print_packages(config_dir: Path, global_config: GlobalConfig) -> None:
    packages = available_packages(config_dir, global_config)
    if not packages:
        write_line("No packages available.")
        return
    write_lines(packages)
