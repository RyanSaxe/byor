"""Resolve BYOR paths and display names.

Path handling crosses repository roots, user configuration directories, and safe relative display
paths. Centralizing those helpers avoids ad hoc path arithmetic in commands and keeps repository-
bound checks defensive.
"""

from __future__ import annotations

import os
from pathlib import Path

from byor.errors import ConfigError

__all__ = (
    "display_path",
    "global_config_dir",
    "home_sgconfig_path",
    "resolve_repo_root",
    "resolve_within",
)


def resolve_within(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):
        msg = f"{candidate} resolves outside {root}"
        raise ConfigError(msg)
    return resolved


def global_config_dir() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "byor"
    return Path.home() / ".config" / "byor"


def home_sgconfig_path(home: Path | None = None) -> Path:
    """ast-grep's home-level global config, `~/sgconfig.yml`.

    ast-grep discovers this only in the home directory (it does not honor
    XDG), so byor's global rules apply in any repo with no `sgconfig.yml` of
    its own. `home` is overridable for tests.
    """
    return (home or Path.home()) / "sgconfig.yml"


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def resolve_repo_root(explicit: Path | None = None, start: Path | None = None) -> Path:
    """Resolve the repository root for repo-operating commands.

    `explicit` (the --repo flag) wins outright. Otherwise search upward from
    `start` (default: cwd), preferring the nearest directory containing
    .byor/config.yml, else the nearest containing .git, else `start` itself.
    """
    if explicit is not None:
        return explicit.resolve()
    base = (start or Path.cwd()).resolve()
    directories = (base, *base.parents)
    for directory in directories:
        if (directory / ".byor" / "config.yml").is_file():
            return directory
    for directory in directories:
        if (directory / ".git").exists():
            return directory
    return base
