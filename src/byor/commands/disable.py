"""Turn byor off and back on for repositories and directory prefixes.

Global rules are opt-out, so the post-edit hook scans every repo — including ones that never ran
`byor init`. Disable entries live only in byor's machine state as resolved absolute paths (a repo root
or an ancestor directory covering many repos); nothing is ever written into the repo itself, which
is the whole point for repos the user does not own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.config import disabled_entry, updating_machine_state
from byor.errors import ByorError
from byor.io.output import write_line
from byor.io.paths import global_state_dir, resolve_repo_root, same_path

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

__all__ = (
    "run_disable",
    "run_enable",
)


def run_disable(args: argparse.Namespace) -> int:
    target = _resolve_target(args.path, must_exist=True)
    with updating_machine_state(global_state_dir()) as state:
        entry = disabled_entry(target, state)
        if entry is None:
            state.disabled_repos.append(target)
    if entry is None:
        write_line(f"Disabled byor under {target}; run `byor enable {target}` to undo")
    elif same_path(entry, target):
        write_line(f"{target} is already disabled")
    else:
        write_line(f"{target} is already disabled by {entry}")
    return 0


def run_enable(args: argparse.Namespace) -> int:
    # A disabled path may have been deleted since, so existence is not required.
    target = _resolve_target(args.path, must_exist=False)
    with updating_machine_state(global_state_dir()) as state:
        named = [entry for entry in state.disabled_repos if same_path(entry, target)]
        for entry in named:
            state.disabled_repos.remove(entry)
        covering = disabled_entry(target, state)
    # A prefix entry covers other paths too, so enable names it rather than lifting it here.
    if named and covering is not None:
        write_line(
            f"Removed the entry for {target}, but the entry {covering} still disables it; "
            f"run `byor enable {covering}` to lift it"
        )
    elif named:
        write_line(f"Enabled byor in {target}")
    elif covering is not None:
        write_line(f"{target} is disabled by the entry {covering}; run `byor enable {covering}` to lift it")
    else:
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
