"""Turn byor off and back on for repositories and directory prefixes.

Global rules are opt-out, so the post-edit hook scans every repo — including ones that never ran
`byor init`. Disable entries live only in the global config as resolved absolute paths (a repo root
or an ancestor directory covering many repos); nothing is ever written into the repo itself, which
is the whole point for repos the user does not own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.config import disabled_entry, load_global_config, save_global_config
from byor.errors import ByorError
from byor.io.output import write_line
from byor.io.paths import global_config_dir, resolve_repo_root

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

__all__ = (
    "run_disable",
    "run_enable",
)


def run_disable(args: argparse.Namespace) -> int:
    target = _resolve_target(args.path, must_exist=True)
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    entry = disabled_entry(target, config)
    if entry == target:
        write_line(f"{target} is already disabled")
        return 0
    if entry is not None:
        write_line(f"{target} is already disabled by {entry}")
        return 0
    config.disabled_repos.append(target)
    save_global_config(config_dir, config)
    write_line(f"Disabled byor under {target}; run `byor enable {target}` to undo")
    return 0


def run_enable(args: argparse.Namespace) -> int:
    # A disabled path may have been deleted since, so existence is not required.
    target = _resolve_target(args.path, must_exist=False)
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    if target in config.disabled_repos:
        config.disabled_repos.remove(target)
        save_global_config(config_dir, config)
        write_line(f"Enabled byor in {target}")
        return 0
    entry = disabled_entry(target, config)
    if entry is not None:
        write_line(f"{target} is disabled by the entry {entry}; run `byor enable {entry}` to lift it")
        return 0
    write_line(f"{target} is not disabled")
    return 0


def _resolve_target(path: Path | None, *, must_exist: bool) -> Path:
    """Resolve the path a disable entry names.

    With no PATH the enclosing repo root is used — for a plain git repo that is
    its top level, and outside git entirely it is the cwd itself, so `byor
    disable` in a bare directory disables exactly that directory.
    """
    if path is None:
        return resolve_repo_root()
    if must_exist and not path.is_dir():
        msg = f"{path} is not a directory"
        raise ByorError(msg)
    return path.resolve()
