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
    "covers_path",
    "display_path",
    "global_config_dir",
    "global_state_dir",
    "home_sgconfig_path",
    "resolve_init_root",
    "resolve_repo_root",
    "resolve_within",
    "same_path",
)


def resolve_within(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):
        msg = f"{candidate} resolves outside {root}"
        raise ConfigError(msg)
    return resolved


def global_config_dir() -> Path:
    return _xdg_base("XDG_CONFIG_HOME", default=Path(".config")) / "byor"


# What byor records about this computer, kept apart from the user's config directory.
def global_state_dir() -> Path:
    return _xdg_base("XDG_STATE_HOME", default=Path(".local") / "state") / "byor"


# The XDG Base Directory spec says to ignore a relative value. Honoring one would
# resolve it against the cwd and scatter byor's files into whatever repo a hook runs in.
def _xdg_base(variable: str, *, default: Path) -> Path:
    value = os.environ.get(variable)
    if value and Path(value).is_absolute():
        return Path(value)
    return Path.home() / default


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

    `explicit` (the --repo flag) wins outright. Otherwise search upward from `start` (default:
    cwd) for the nearest directory holding either `.git` (a folder, or the file of a linked
    worktree) or `.byor/config.yml`, else `start` itself. Nearest wins, so a git repo nested in a
    byor repo, a submodule, or a worktree placed inside its main checkout (`.worktrees/x`) is its
    own root and never takes the outer repo's rules or settings.
    """
    if explicit is not None:
        return explicit.resolve()
    base = (start or Path.cwd()).resolve()
    for directory in (base, *base.parents):
        if (directory / ".git").exists() or (directory / ".byor" / "config.yml").is_file():
            return directory
    return base


def resolve_init_root(explicit: Path | None = None) -> Path:
    """Resolve the directory `byor init` sets up, like `resolve_repo_root`.

    An explicit --repo must already be a directory. Without this check a typo such as
    `byor init --repo nothere/sub` created the whole tree and registered it, because the atomic
    writer makes missing parents, and a --repo naming a file crashed with a raw NotADirectoryError.
    Both now raise ConfigError with one line naming the path. Other commands need no check: they
    report a missing --repo as a repo without `.byor/config.yml`.
    """
    if explicit is not None and not explicit.exists():
        msg = f"{explicit} does not exist; create the repository first"
        raise ConfigError(msg)
    if explicit is not None and not explicit.is_dir():
        msg = f"{explicit} is not a directory"
        raise ConfigError(msg)
    return resolve_repo_root(explicit=explicit)


def same_path(first: Path, second: Path) -> bool:
    """Whether two resolved paths name the same directory.

    `Path.resolve()` keeps the case the user typed, so on a case-insensitive volume, the macOS and
    Windows default, `~/Clients` and `~/clients` are one directory with two spellings. Paths that
    differ only in case are compared on disk; a path that does not exist matches only its exact
    spelling.
    """
    if first == second:
        return True
    if first.as_posix().casefold() != second.as_posix().casefold():
        return False
    try:
        return first.samefile(second)
    except OSError:
        return False


def covers_path(entry: Path, path: Path) -> bool:
    """Whether the resolved directory `entry` is `path` or one of its ancestors.

    This is the test a `byor disable` entry uses to cover a repo. The string comparison handles
    the common case; when it fails, the prefix of `path` that is as deep as `entry` is compared
    with `same_path`, so a disabled `~/Clients` also covers `~/clients/c1` on a case-insensitive
    volume. The prefix is only stat'ed when it differs from `entry` in case alone, so hooks in
    ordinary repos pay nothing for it. `/foo` never covers `/foobar`.
    """
    if path.is_relative_to(entry):
        return True
    depth = len(entry.parts)
    return len(path.parts) >= depth and same_path(Path(*path.parts[:depth]), entry)
